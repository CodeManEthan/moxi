"""The resolvers a page's check may use read the environment only through the mapping they
are given, and only names of the set the core records.

The resolvers: every `Layout` method, `paths.core()`, `paths.core_lock`, `paths.home`,
`paths._expanduser`, `place.folders` and `paths.which`. Each runs over a mapping that
notes every name read, on Linux and macOS and on Windows (the core's Windows branch, with
the shell's folders made up), and under a process environment poisoned in every name the
set holds, so a direct read of the process shows as a wrong answer.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from moxi import _win, paths, place
from moxi.paths import Layout

#: The modules whose layouts run, beside the core's own.
MODULES = ("cache", "projects")

# The names the core records, matched by name. The core's record defines the set; this
# copy says the same, so a resolver that starts reading another name fails here first.
FIXED = frozenset({
    "MOXI_HOME", "MOXI_DEVICE", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR", "HOME", "WIN_PD_OVERRIDE_APPDATA",
    "WIN_PD_OVERRIDE_LOCAL_APPDATA", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
    "CLAUDE_CONFIG_DIR", "CODEX_HOME", "PATH", "PATHEXT"})
MODULE_NAME = re.compile(r"MOXI_[A-Z0-9_]+_(?:%s)" % "|".join((*paths.LOCATORS, "DEVICE")))


def in_set(name: str) -> bool:
    return name in FIXED or bool(MODULE_NAME.fullmatch(name))


class Noting(Mapping):
    """A mapping that notes each name read, and refuses to be iterated: a resolver reads
    names, it doesn't list the environment."""

    def __init__(self, data: Mapping[str, str]):
        self.data = dict(data)
        self.read: set[str] = set()

    def __getitem__(self, key: str) -> str:
        self.read.add(key)
        return self.data[key]

    def get(self, key, default=None):
        self.read.add(key)
        return self.data.get(key, default)

    def __contains__(self, key) -> bool:
        self.read.add(key)
        return key in self.data

    def __iter__(self) -> Iterator[str]:
        raise AssertionError("a resolver iterated the environment")

    def __len__(self) -> int:
        raise AssertionError("a resolver measured the environment")


def every_name(base: Path) -> dict[str, str]:
    """A value under `base` for every name of the set the resolvers here can read."""
    names = sorted(FIXED - {"PATH", "PATHEXT", "HOMEDRIVE"})
    for module in MODULES:
        prefix = f"MOXI_{module.upper()}_"
        names += [prefix + suffix for suffix in (*paths.LOCATORS, "DEVICE")]
    values = {name: str(base / name.lower()) for name in names}
    values["MOXI_DEVICE"] = f"device-{base.name}"
    values["HOMEDRIVE"] = ""
    values["PATH"] = str(base / "bin")
    values["PATHEXT"] = ".EXE"
    return values


def scenarios(base: Path) -> dict[str, dict[str, str]]:
    """Mappings that reach each branch: every name set, only the defaults' inputs, only a
    home, a home that is empty, and nothing at all."""
    full = every_name(base)
    defaults = {k: v for k, v in full.items()
                if k.startswith(("XDG_", "WIN_PD_")) or k in ("HOME", "USERPROFILE", "PATH")}
    return {
        "every name": full,
        "defaults": defaults,
        "a home only": {"HOME": full["HOME"], "USERPROFILE": full["USERPROFILE"]},
        "an empty home": {"HOME": "", "USERPROFILE": "", "HOMEPATH": "",
                          "WIN_PD_OVERRIDE_APPDATA": "", "WIN_PD_OVERRIDE_LOCAL_APPDATA": ""},
        "nothing": {},
    }


def resolve(env) -> dict[str, object]:
    """Every resolver's answer under `env`, by name."""
    found: dict[str, object] = {}
    layouts = {"core": paths.core(), **{m: Layout(m) for m in MODULES}}
    for name, layout in layouts.items():
        for method in ("data_root", "state_root", "cache_root", "config_root",
                       "runtime_root", "data_dir", "state_dir", "cache_dir", "config_dir",
                       "runtime_dir", "config_file"):
            found[f"{name}.{method}"] = getattr(layout, method)(env)
        found[f"{name}.device"] = layout.device(env, create=False)
        found[f"{name}.locators"] = layout.locators()
    found["core_lock"] = paths.core_lock(env)
    try:
        found["home"] = paths.home(env)
    except LookupError as exc:
        found["home"] = repr(exc)
    found["expanduser"] = paths._expanduser("~/x", os.environ if env is None else env)
    found["folders"] = place.folders(env)
    found["which"] = paths.which("tool", env)
    return found


@pytest.fixture(params=["posix", "windows"])
def platform(request, monkeypatch, tmp_path):
    """The core on Linux and macOS, or on Windows with the shell's folders under tmp."""
    if request.param == "windows":
        shell = {name: str(tmp_path / "shell" / name) for name in _win.KNOWN_FOLDERS}
        monkeypatch.setattr(paths, "_WINDOWS", True)
        monkeypatch.setattr(_win, "known_folder", lambda name: shell[name])
    elif os.name == "nt":
        pytest.skip("the POSIX branch reads the user database")
    return request.param


def place_world(values: Mapping[str, str], row: str, base: Path) -> None:
    """A program `tool` on the mapping's PATH, and a core settings file where the mapping
    places it, holding a harness row named `row`."""
    for folder in filter(None, (values.get("PATH") or "").split(os.pathsep)):
        Path(folder).mkdir(parents=True, exist_ok=True)
        for name in ("tool", "tool.EXE"):
            (Path(folder) / name).write_text("#!/bin/sh\n")
            (Path(folder) / name).chmod(0o755)
    try:
        config = paths.core().config_file(values)
    except LookupError:
        return
    if not config.is_absolute() or not config.is_relative_to(base):
        return  # no folder given: the account's own, which no test writes under
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        'harnesses = [{ name = "claude", skills = "", agents = "" },\n'
        '             { name = "codex", skills = "", agents = "" },\n'
        f'             {{ name = "{row}", skills = "~/.{row}/skills", agents = "" }}]\n')


@pytest.fixture
def poisoned(tmp_path, monkeypatch) -> dict[str, str]:
    """The process's environment set to other values in every name of the set, with a
    settings file and a `tool` of its own."""
    poison = every_name(tmp_path / "poison")
    for key in [k for k in os.environ if in_set(k) or k in ("MOXI_ANCHOR_HOME",)]:
        monkeypatch.delenv(key)
    for key, value in poison.items():
        monkeypatch.setenv(key, value)
    place_world(poison, "poison", tmp_path)
    return poison


# -- the core set -------------------------------------------------------------------------

@pytest.mark.parametrize("scenario", list(scenarios(Path("/s"))))
def test_every_resolver_reads_only_names_in_the_set(platform, poisoned, tmp_path, scenario):
    values = scenarios(tmp_path / "given")[scenario]
    place_world(values, "given", tmp_path)
    noting = Noting(values)
    resolve(noting)
    outside = sorted(name for name in noting.read if not in_set(name))
    assert outside == [], f"read outside the set: {outside}"
    assert noting.read, "nothing was read through the mapping"


def test_the_set_holds_what_the_table_names():
    assert in_set("MOXI_PROJECTS_DATA_HOME") and in_set("MOXI_X_RUNTIME_DIR")
    assert in_set("MOXI_CACHE_DEVICE") and in_set("PATHEXT")
    assert not in_set("UV_TOOL_DIR") and not in_set("APPDATA")
    assert not in_set("LOCALAPPDATA") and not in_set("MOXI_CALLER")


# -- resolvers follow the mapping ---------------------------------------------------------

@pytest.mark.parametrize("scenario", list(scenarios(Path("/s"))))
def test_every_resolver_answers_from_the_mapping_not_the_process(
        platform, poisoned, tmp_path, monkeypatch, scenario):
    values = scenarios(tmp_path / "given")[scenario]
    place_world(values, "given", tmp_path)
    given = resolve(dict(values))
    # The same mapping as the process's own environment gives the same answers: so
    # nothing above read the poisoned process.
    for key in list(os.environ):
        monkeypatch.delenv(key)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    assert resolve(None) == given
    poison = str(tmp_path / "poison")
    leaked = {k: v for k, v in given.items() if poison in str(v)}
    assert leaked == {}


def test_what_the_mapping_places_on_linux_and_macos(platform, poisoned, tmp_path):
    if platform != "posix":
        pytest.skip("the POSIX answers")
    import pwd

    values = scenarios(tmp_path / "given")["every name"]
    place_world(values, "given", tmp_path)
    got = resolve(values)
    assert got["cache.data_dir"] == Path(values["MOXI_CACHE_DATA_HOME"], "cache")
    assert got["core.config_file"] == Path(values["MOXI_HOME"], "config", "moxi",
                                           "config.toml")
    assert got["home"] == Path(values["HOME"])
    assert got["which"] == str(Path(values["PATH"], "tool"))
    assert got["folders"] == {
        "claude's skills folder": Path(values["CLAUDE_CONFIG_DIR"], "skills"),
        "claude's agents folder": Path(values["CLAUDE_CONFIG_DIR"], "agents"),
        "codex's skills folder": Path(values["HOME"], ".agents", "skills"),
        "codex's agents folder": Path(values["CODEX_HOME"], "agents"),
        "given's skills folder": Path(values["HOME"], ".given", "skills")}
    account = pwd.getpwuid(os.getuid()).pw_dir
    for scenario in ("an empty home", "nothing"):
        got = resolve(scenarios(tmp_path / "given")[scenario])
        assert got["home"] == Path(account)
        assert got["expanduser"] == os.path.join(account, "x")
        assert got["core.data_dir"] == Path(account, ".local/share/moxi/moxi")


def test_what_the_mapping_places_on_windows(platform, poisoned, tmp_path):
    if platform != "windows":
        pytest.skip("the Windows answers")
    shell = tmp_path / "shell"
    values = scenarios(tmp_path / "given")["defaults"]
    got = resolve(values)
    assert got["cache.data_dir"] == Path(values["WIN_PD_OVERRIDE_LOCAL_APPDATA"], "moxi",
                                         "data", "cache")
    assert got["core.config_dir"] == Path(values["WIN_PD_OVERRIDE_APPDATA"], "moxi",
                                          "config", "moxi")
    assert got["home"] == Path(values["USERPROFILE"])
    # An empty override reads as unset: the shell's folders, not the poisoned ones.
    got = resolve(scenarios(tmp_path / "given")["an empty home"])
    assert got["cache.data_dir"] == Path(shell / "local", "moxi", "data", "cache")
    assert got["core.config_dir"] == Path(shell / "roaming", "moxi", "config", "moxi")
    assert got["home"] == Path(shell / "profile")
