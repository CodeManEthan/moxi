"""Under a test, rounds refuses its directories when no Moxi variable places them, and a
project's folders when no Moxi variable places the projects registry: those are the real
ones. Fenced tests and runs that are not tests are left alone. A scan of rounds' source
holds every write to one of the choke points, and every broad handler to re-raising the
refusal."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest
from moxi.paths import Layout

from moxi_rounds import (folders, index, opening, paths, places, queue_file, removal,
                         settings, sweeping, write)
from moxi_rounds.cli import main
from moxi_rounds.errors import RoundsError, UnfencedTest

SOURCE = Path(paths.__file__).parent
KINDS = ("data", "state", "cache", "config", "runtime")
ROUNDS_LOCATORS = ("MOXI_ROUNDS_HOME", "MOXI_ROUNDS_DATA_HOME", "MOXI_ROUNDS_STATE_HOME",
                   "MOXI_ROUNDS_CACHE_HOME", "MOXI_ROUNDS_CONFIG_HOME",
                   "MOXI_ROUNDS_RUNTIME_DIR", "MOXI_HOME")


# -- the signal and the rule, on given mappings -----------------------------------------

@pytest.mark.parametrize("env, modules, found", [
    ({"PYTEST_CURRENT_TEST": "t.py::test (call)"}, {}, "PYTEST_CURRENT_TEST is set"),
    ({"PYTEST_VERSION": "9.1.1"}, {}, "PYTEST_VERSION is set"),
    ({}, {"pytest": object()}, "pytest is imported"),
    ({}, {}, None),
    ({"PYTEST_XDIST_WORKER": "gw0"}, {"_pytest": object()}, None),
])
def test_each_sign_of_a_test_is_found_alone_and_none_gives_none(env, modules, found):
    assert paths.running_under_pytest(env, modules) == found


@pytest.mark.parametrize("layout, module_home", [
    (paths.LAYOUT, "MOXI_ROUNDS_HOME"), (paths.PROJECTS_LAYOUT, "MOXI_PROJECTS_HOME")])
@pytest.mark.parametrize("kind", KINDS)
def test_a_root_no_moxi_variable_places_is_refused_under_a_test(layout, module_home, kind,
                                                                 tmp_path):
    suffix = "RUNTIME_DIR" if kind == "runtime" else f"{kind.upper()}_HOME"
    own = f"{layout.prefix}{suffix}"
    stand_in = {"XDG_CONFIG_HOME": str(tmp_path / "xdg")}
    text = paths.refused_root(layout, kind, stand_in, "PYTEST_VERSION is set")
    assert text is not None
    assert f"{layout.module} refused its {kind} directory" in text
    assert "PYTEST_VERSION is set" in text and f"set {module_home} to" in text
    for variable in (own, module_home, "MOXI_HOME"):
        placed = {**stand_in, variable: str(tmp_path / "placed")}
        assert paths.refused_root(layout, kind, placed, "PYTEST_VERSION is set") is None
        assert paths.refused_root(layout, kind, {**stand_in, variable: ""},
                                  "PYTEST_VERSION is set") is not None
    assert paths.refused_root(layout, kind, stand_in, None) is None


def test_another_modules_root_variable_does_not_place_rounds(tmp_path):
    env = {"MOXI_PROJECTS_HOME": str(tmp_path), "MOXI_ROUNDS_CONFIG_HOME": str(tmp_path)}
    assert paths.refused_root(paths.LAYOUT, "data", env, "pytest is imported") is not None
    assert paths.refused_root(paths.LAYOUT, "config", env, "pytest is imported") is None


# -- rounds' own directories, under the fence -------------------------------------------

def _writers():
    """Each of rounds' writers, by name, as the call that resolves where it writes."""
    return {
        "config read": settings.load,
        "index open": lambda: index.rebuild([]),
        "round lock": lambda: write.round_lock("proj-a", "a-round"),
        "project lock": lambda: write.project_lock("proj-a"),
        "queue": lambda: queue_file.queue_dir("proj-a"),
        "scratch": places.scratch_root,
        "sweep trash": removal.trash_dir,
        "sweep start file": sweeping.started_file,
        "sweep log": sweeping.log_file,
        "sweep lock": sweeping.lock_file,
        "default worktrees": paths.default_worktree_dir,
        "runtime": paths.runtime_dir,
        "config file": paths.LAYOUT.config_file,
    }


def _moxi_dirs(env) -> list[Path]:
    """The `moxi` folder that holds each of rounds' five roots as the platform's defaults
    place them under `env`'s home, which no Moxi variable moves: the folder itself on
    XDG systems, its parent where the root kind is a subfolder (Windows)."""
    plain = Layout(paths.MODULE)
    bare = {key: value for key, value in env.items() if key not in ROUNDS_LOCATORS}
    roots = [getattr(plain, f"{kind}_root")(bare) for kind in KINDS]
    return sorted({next(ancestor for ancestor in (root, *root.parents)
                        if ancestor.name == plain.namespace) for root in roots})


def _strays(fence) -> list[Path]:
    """What the default `moxi` folders (`_moxi_dirs`) hold that is no module home the
    fence gave. On Windows the fence keeps each module's home in that folder under the
    test's own profile, so a writer that lands in the fence's home makes the folder too;
    elsewhere the folder is never the fence's, and its being there is the stray."""
    def real(path):
        return os.path.normcase(os.path.realpath(path))

    found = []
    for moxi_dir in _moxi_dirs(os.environ):
        if not os.path.lexists(moxi_dir):
            continue
        if real(moxi_dir) != real(fence.module_home(paths.MODULE).parent):
            found.append(moxi_dir)
            continue
        found += [entry for entry in moxi_dir.iterdir()
                  if real(entry) != real(fence.module_home(entry.name))]
    return found


@pytest.mark.parametrize("writer", sorted(_writers()))
def test_each_writer_is_refused_when_no_moxi_variable_places_rounds(writer, monkeypatch,
                                                                    fence):
    monkeypatch.delenv("MOXI_ROUNDS_HOME")
    with pytest.raises(UnfencedTest, match="a test is running") as refused:
        _writers()[writer]()
    assert not isinstance(refused.value, (RoundsError, OSError))
    assert "set MOXI_ROUNDS_HOME to a folder of the test's own" in str(refused.value)
    assert [moxi_dir for moxi_dir in _moxi_dirs(os.environ) if os.path.lexists(moxi_dir)] == []


@pytest.mark.parametrize("writer", sorted(_writers()))
def test_each_writer_lands_in_the_fences_home(writer, fence):
    home = os.path.realpath(fence.module_home(paths.MODULE))
    found = _writers()[writer]()
    if isinstance(found, Path):
        assert os.path.realpath(found).startswith(home + os.sep)
    assert _strays(fence) == []


def test_the_cli_prints_the_refusal_and_exits_1(monkeypatch, capsys):
    monkeypatch.delenv("MOXI_ROUNDS_HOME")
    assert main(["list"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("moxi rounds list: rounds refused its config directory")


def test_a_run_that_is_not_a_test_writes_to_the_default_roots(fence, tmp_path):
    env = {key: value for key, value in fence.env.items()
           if key not in ("PYTEST_CURRENT_TEST", "PYTEST_VERSION")
           and not (key.startswith("MOXI_") and key.endswith("_HOME")
                    and key != "MOXI_ANCHOR_HOME")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    code = ("from moxi_rounds import index, paths\n"
            "config = paths.LAYOUT.config_file()\n"
            "config.parent.mkdir(parents=True)\n"
            "config.write_text('', encoding='utf-8')\n"
            "index.rebuild([])\n")
    done = subprocess.run([sys.executable, "-c", code], env=env, cwd=tmp_path,
                          capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stderr
    plain = Layout(paths.MODULE)
    for made in (plain.config_file(env), plain.data_dir(env) / paths.INDEX_FILE):
        assert made.is_file()
        assert os.path.realpath(made).startswith(os.path.realpath(fence.home) + os.sep)


# -- a project's folders from the registry ----------------------------------------------

def _without_signal(env) -> dict[str, str]:
    return {key: value for key, value in env.items()
            if key not in ("PYTEST_CURRENT_TEST", "PYTEST_VERSION")}


@pytest.fixture
def real_registry(monkeypatch, tmp_path, add_project):
    """The project `proj-a` in a registry no Moxi variable places, at the platform's
    default under the test's home; rounds' own homes stay the fence's. Returns the
    project's notes folder."""
    monkeypatch.delenv("MOXI_PROJECTS_HOME")
    notes = tmp_path / "notes"
    add_project("proj-a", notes_root=notes)
    registry = Layout("projects").data_dir(os.environ)
    assert os.path.realpath(registry).startswith(os.path.realpath(tmp_path) + os.sep)
    return notes


@pytest.mark.parametrize("read", [
    lambda: opening.open_round("proj-a/a-round", description="a round", by="planner"),
    lambda: folders.home("proj-a"),
    lambda: folders.notes_dir("proj-a"),
    lambda: folders.root("proj-a"),
    lambda: folders.roots(),
])
def test_a_projects_places_are_refused_when_the_registry_is_the_real_one(read,
                                                                         real_registry):
    with pytest.raises(UnfencedTest, match="from the projects registry") as refused:
        read()
    assert "set MOXI_PROJECTS_HOME to a folder of the test's own" in str(refused.value)
    assert sorted(real_registry.rglob("*")) == []


def test_another_process_without_the_signal_writes_the_same_notes(
        real_registry, tmp_path):
    with pytest.raises(UnfencedTest):
        opening.open_round("proj-a/a-round", description="a round", by="planner")
    assert not (real_registry / "rounds" / "a-round").exists()
    env = {**_without_signal(os.environ), "PYTHONDONTWRITEBYTECODE": "1"}
    code = ("import sys\n"
            "from moxi_rounds.cli import main\n"
            "sys.exit(main(['open', 'proj-a/a-round', '--description', 'a round']))\n")
    done = subprocess.run([sys.executable, "-c", code], env=env, cwd=tmp_path,
                          capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stderr
    assert (real_registry / "rounds" / "a-round" / "round.jsonl").is_file()


# -- the scan of rounds' source ---------------------------------------------------------

#: What may resolve a place outside `paths` and `folders._projects`, by file, function
#: and kind, with the number of uses and why each is allowed. Each is a path a user or a
#: record gave, read with `~` as the home folder; none places one of rounds' own files.
ALLOWED = {
    ("export.py", "write", "expanduser"): (1, "the export folder the user names"),
    ("settings.py", "_check_dir", "expanduser"): (1, "a project's `dir` setting"),
    ("settings.py", "_check_rounds_dirs_apart", "expanduser"): (1, "the same setting"),
    ("folders.py", "root", "expanduser"): (1, "the root a project's record names"),
    ("folders.py", "roots", "expanduser"): (1, "the same, for every project"),
    ("folders.py", "project_root_at", "expanduser"): (1, "the same, for a written file"),
    ("folders.py", "_rounds_dir", "expanduser"): (1, "a project's `dir` setting"),
    ("folders.py", "_notes_dir", "expanduser"): (1, "the notes folder a record names"),
    ("places.py", "expand_home", "expanduser"): (1, "a `~` path a round's record holds"),
    ("places.py", "contract_home", "expanduser"): (1, "the home folder, to write `~`"),
    ("pagehandlers.py", "_home", "Path.home"): (1, "the home folder the page shows"),
}

#: The core's resolvers of places outside rounds' layout, refused outside `paths.py`.
_CORE_PLACES = {"Layout", "core", "core_lock", "socket_path", "device_label", "device_id",
                "home", "claude_home", "anchor_dir", "terminal_record"}
_BROAD = {"Exception", "BaseException"}


def _is_broad(handler: ast.ExceptHandler) -> bool:
    caught = handler.type
    if caught is None:
        return True
    names = caught.elts if isinstance(caught, ast.Tuple) else [caught]
    return [name for name in names if ast.unparse(name).rsplit(".", 1)[-1] in _BROAD] != []


def _reraises(handler: ast.ExceptHandler) -> bool:
    return (handler.type is not None
            and ast.unparse(handler.type).rsplit(".", 1)[-1] == "UnfencedTest"
            and len(handler.body) == 1 and isinstance(handler.body[0], ast.Raise)
            and handler.body[0].exc is None)


def scan(name: str, tree: ast.Module) -> list[tuple[str, str, str]]:
    """Each use in the module `name` that bypasses the choke points, as (file, function,
    kind): one `ALLOWED` names, or one the scan refuses."""
    found = []
    config_names = ["moxi.config"]
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "moxi":
            config_names += [alias.asname or alias.name for alias in node.names
                             if alias.name == "config"]

    def visit(node: ast.AST, function: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name
        hit = lambda kind: found.append((name, function, kind))  # noqa: E731
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            taken = {alias.name for alias in node.names}
            if module == "moxi.paths" and name != "paths.py" and taken & _CORE_PLACES:
                hit("moxi.paths " + ", ".join(sorted(taken & _CORE_PLACES)))
            if module == "moxi" and taken & {"run", "ipc"}:
                hit("moxi.run or moxi.ipc")
            if module == "moxi" and "paths" in taken:
                hit("moxi.paths as a module")
            if module.startswith(("moxi.run", "moxi.ipc", "platformdirs", "moxi_projects")):
                hit(module)
            if (module == "moxi.config" and name != "settings.py"
                    and taken - {"ConfigError"}):
                hit("moxi.config " + ", ".join(sorted(taken - {"ConfigError"})))
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(("moxi.run", "moxi.ipc", "platformdirs",
                                          "moxi_projects")):
                    hit(alias.name)
        if isinstance(node, ast.Attribute):
            owner = ast.unparse(node.value)
            if owner == "moxi.paths" and node.attr in _CORE_PLACES:
                hit(f"moxi.paths.{node.attr}")
            if node.attr == "expanduser":
                hit("expanduser")
            if owner == "Path" and node.attr == "home":
                hit("Path.home")
            if (owner in config_names and node.attr != "ConfigError"
                    and name != "settings.py"):
                hit(f"moxi.config.{node.attr}")
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "load" and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "projects"
                and (name, function) != ("folders.py", "_projects")):
            hit("the projects registry")
        if isinstance(node, ast.Call) and ast.unparse(node.func).endswith("suppress"):
            broad = [arg for arg in node.args
                     if ast.unparse(arg).rsplit(".", 1)[-1] in _BROAD]
            if broad:
                hit("a broad suppress")
        if isinstance(node, (ast.Try, getattr(ast, "TryStar", ast.Try))):
            for position, handler in enumerate(node.handlers):
                first = [earlier for earlier in node.handlers[:position]
                         if _reraises(earlier)]
                if _is_broad(handler) and not first:
                    found.append((name, function, f"a broad handler, line "
                                                  f"{handler.lineno}"))
        for child in ast.iter_child_nodes(node):
            visit(child, function)

    visit(tree, "<module>")
    return found


def _source_hits() -> dict[tuple[str, str, str], int]:
    counted: dict[tuple[str, str, str], int] = {}
    for path in sorted(SOURCE.rglob("*.py")):
        name = path.relative_to(SOURCE).as_posix()
        for hit in scan(name, ast.parse(path.read_text(encoding="utf-8"))):
            counted[hit] = counted.get(hit, 0) + 1
    return counted


def test_nothing_in_rounds_places_a_file_past_the_choke_points():
    counted = _source_hits()
    refused = {hit: count for hit, count in counted.items() if hit not in ALLOWED}
    assert refused == {}
    assert {hit: count for hit, count in counted.items()} == {
        hit: count for hit, (count, _why) in ALLOWED.items()}


@pytest.mark.parametrize("source, kind", [
    ("try:\n    f()\nexcept Exception:\n    pass\n", "a broad handler, line 3"),
    ("try:\n    f()\nexcept:\n    pass\n", "a broad handler, line 3"),
    ("try:\n    f()\nexcept (OSError, Exception):\n    pass\n", "a broad handler, line 3"),
    ("try:\n    f()\nexcept UnfencedTest:\n    g()\nexcept BaseException:\n    pass\n",
     "a broad handler, line 5"),
    ("with contextlib.suppress(Exception):\n    f()\n", "a broad suppress"),
    ("from moxi.paths import Layout\n", "moxi.paths Layout"),
    ("import moxi.paths\nmoxi.paths.device_id()\n", "moxi.paths.device_id"),
    ("from moxi import run\n", "moxi.run or moxi.ipc"),
    ("import platformdirs\n", "platformdirs"),
    ("from moxi import config\nconfig.load('rounds')\n", "moxi.config.load"),
    ("from moxi import modules\nmodules.load('projects', interface=1)\n",
     "the projects registry"),
    ("from pathlib import Path\nPath.home()\n", "Path.home"),
])
def test_the_scan_refuses_each_kind_of_bypass(source, kind):
    assert [hit[2] for hit in scan("elsewhere.py", ast.parse(source))] == [kind]


def test_the_scan_lets_a_handler_through_that_re_raises_the_refusal_first():
    source = ("try:\n    f()\nexcept UnfencedTest:\n    raise\n"
              "except Exception:\n    pass\n"
              "from moxi.config import ConfigError\n")
    assert scan("elsewhere.py", ast.parse(source)) == []
