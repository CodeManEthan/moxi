"""The hook path: `load` reads its distribution's .dist-info directly, and stays light.

A hook pays for `import moxi.paths`, `available()` and `load()`. `load` reads the
`moxi-<name>` distribution's `.dist-info` directory itself rather than importing
`importlib.metadata`, so these tests hold the direct read equal to what
`importlib.metadata.distribution` finds, and hold the path free of the heavy imports.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import moxi.modules as modules
from moxi import names
from moxi.modules import ABSENT, BROKEN, GROUP, PRESENT

#: What the hook path must not load.
HEAVY = ("importlib.metadata", "dataclasses", "inspect", "typing", "zipfile", "json")

REINSTALL = ("not installed as a .dist-info directory on a sys.path directory; reinstall "
             "with pip or uv")


def metadata_text(name: str, version: str) -> str:
    # The body after the blank line repeats a header, which a reader of the headers ignores.
    return (f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n\n"
            "A long description.\nVersion: 9.9\n")


def entry_points_text(entries: dict[str, str]) -> str:
    return ("[console_scripts]\nsome-tool = some.module:main\n\n[moxi.modules]\n"
            + "".join(f"{k} = {v}\n" for k, v in entries.items()))


def write_dist(root: Path, dirname: str, *, name: str, version: str,
               entries: dict[str, str] | None = None) -> Path:
    """A metadata directory `dirname` under `root`, with METADATA and entry points."""
    info = root / dirname
    info.mkdir(parents=True)
    (info / "METADATA").write_text(metadata_text(name, version))
    if entries is not None:
        (info / "entry_points.txt").write_text(entry_points_text(entries))
    return info


def write_package(root: Path, package: str) -> None:
    (root / package).mkdir(parents=True)
    (root / package / "__init__.py").write_text("")
    (root / package / "module.py").write_text("SUMMARY = 'A module made for a test.'\n")


def fast(name: str):
    """What `load`'s direct read finds: the distribution's name, version and entry."""
    dist = modules._DistInfo.find(name)
    if dist is None:
        return None
    return dist.name, dist.version, dist.entry(name)


def reference(name: str):
    """What `importlib.metadata.distribution("moxi-<name>")` finds, in the same shape."""
    from importlib.metadata import PackageNotFoundError, distribution

    try:
        dist = distribution(names.distribution(name))
    except PackageNotFoundError:
        return None
    eps = list(dist.entry_points.select(group=GROUP, name=name))
    return dist.metadata["Name"], dist.version, eps[0].value if eps else None


@pytest.fixture
def fresh(monkeypatch):
    """Forget every load record before and after, and drop the test's packages after."""
    modules.reset()
    before = set(sys.modules)
    yield
    modules.reset()
    for mod in set(sys.modules) - before:
        if mod.split(".")[0].startswith("moxi_hp"):
            del sys.modules[mod]


# --- the direct read equals importlib.metadata -------------------------------------------

def test_the_direct_read_equals_importlib_metadata_for_the_installed_modules():
    from importlib.metadata import entry_points

    installed = sorted({ep.name for ep in entry_points(group=GROUP)
                        if names.is_module_name(ep.name)})
    if not installed:
        pytest.skip("this environment installs no Moxi module")
    for name in installed:
        assert fast(name) == reference(name), name
        assert fast(name) is not None, name


@pytest.mark.parametrize("order", ["first", "second"])
def test_of_two_copies_in_two_directories_the_first_on_sys_path_is_read(
        tmp_path, monkeypatch, order):
    first, second = tmp_path / "first", tmp_path / "second"
    write_dist(first, "moxi_hptwo-1.0.dist-info", name="moxi-hptwo", version="1.0",
               entries={"hptwo": "moxi_hptwo_first.module"})
    write_dist(second, "moxi_hptwo-2.0.dist-info", name="moxi-hptwo", version="2.0",
               entries={"hptwo": "moxi_hptwo_second.module"})
    ahead, behind = (first, second) if order == "first" else (second, first)
    monkeypatch.syspath_prepend(str(behind))
    monkeypatch.syspath_prepend(str(ahead))
    want = (("moxi-hptwo", "1.0", "moxi_hptwo_first.module") if order == "first"
            else ("moxi-hptwo", "2.0", "moxi_hptwo_second.module"))
    assert fast("hptwo") == reference("hptwo") == want


@pytest.mark.parametrize("dirname, name, found", [
    ("Moxi__HpOdd-3.0.dist-info", "Moxi__HpOdd", True),
    ("Moxi.HpOdd-3.0.dist-info", "Moxi.HpOdd", True),
    ("moxi_hpodd-3.0.DIST-INFO", "moxi-hpodd", True),
    ("Moxi_HpOdd-3.0.Dist-Info", "Moxi_HpOdd", True),
    # A hyphen left unescaped ends the name: neither reader takes this one.
    ("moxi-hpodd-3.0.dist-info", "moxi-hpodd", False),
])
def test_a_directory_name_in_another_spelling_reads_the_same(tmp_path, monkeypatch, dirname,
                                                             name, found):
    write_dist(tmp_path, dirname, name=name, version="3.0",
               entries={"hpodd": "moxi_hpodd.module"})
    monkeypatch.syspath_prepend(str(tmp_path))
    assert fast("hpodd") == reference("hpodd")
    assert (fast("hpodd") is not None) == found


def test_an_entry_with_extras_reads_the_same_and_loads(tmp_path, monkeypatch, fresh):
    write_dist(tmp_path, "moxi_hpext-1.0.dist-info", name="moxi-hpext", version="1.0",
               entries={"hpext": "moxi_hpext.module [extra]"})
    write_package(tmp_path, "moxi_hpext")
    monkeypatch.syspath_prepend(str(tmp_path))
    assert fast("hpext") == reference("hpext") == (
        "moxi-hpext", "1.0", "moxi_hpext.module [extra]")
    found = modules.load("hpext")
    assert found.state == PRESENT, found.reason
    assert found.package == "moxi_hpext"
    assert found.target == "moxi_hpext.module [extra]"


def test_the_version_is_metadata_s_when_the_directory_says_otherwise(tmp_path, monkeypatch):
    write_dist(tmp_path, "moxi_hpver-1.0.dist-info", name="moxi-hpver", version="1.0.0",
               entries={"hpver": "moxi_hpver.module"})
    monkeypatch.syspath_prepend(str(tmp_path))
    assert fast("hpver") == reference("hpver") == (
        "moxi-hpver", "1.0.0", "moxi_hpver.module")


def test_an_empty_sys_path_entry_is_the_current_directory(tmp_path, monkeypatch):
    write_dist(tmp_path, "moxi_hpcwd-1.0.dist-info", name="moxi-hpcwd", version="1.0",
               entries={"hpcwd": "moxi_hpcwd.module"})
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend("")
    assert fast("hpcwd") == reference("hpcwd") == ("moxi-hpcwd", "1.0", "moxi_hpcwd.module")


def test_a_distribution_without_the_entry_reads_the_same(tmp_path, monkeypatch):
    write_dist(tmp_path, "moxi_hpnone-1.0.dist-info", name="moxi-hpnone", version="1.0",
               entries={"other": "moxi_hpnone.module"})
    monkeypatch.syspath_prepend(str(tmp_path))
    assert fast("hpnone") == reference("hpnone") == ("moxi-hpnone", "1.0", None)
    assert fast("hpnothere") is None and reference("hpnothere") is None


def test_with_no_metadata_file_the_directory_gives_the_name_and_version(tmp_path,
                                                                        monkeypatch):
    info = tmp_path / "moxi_hpbare-2.5.dist-info"
    info.mkdir()
    (info / "entry_points.txt").write_text(entry_points_text({"hpbare": "moxi_hpbare.m"}))
    monkeypatch.syspath_prepend(str(tmp_path))
    assert fast("hpbare") == ("moxi_hpbare", "2.5", "moxi_hpbare.m")


# --- installed any other way: absent to load, broken to the full scan ---------------------

def _finder(name: str, entries: dict[str, str]):
    """A `sys.meta_path` finder that supplies distribution `name` and no path for it."""
    from importlib.metadata import Distribution

    class FoundByFinder(Distribution):
        def read_text(self, filename):
            return {"METADATA": metadata_text(name, "1.0"),
                    "entry_points.txt": entry_points_text(entries)}.get(filename)

        def locate_file(self, path):
            return Path("/nonexistent") / path

    class Finder:
        @staticmethod
        def find_spec(*args, **kwargs):
            return None

        @staticmethod
        def find_distributions(context=None):
            wanted = getattr(context, "name", None)
            if wanted is None or wanted.replace("_", "-").lower() == name:
                yield FoundByFinder()

    return Finder()


def test_an_egg_info_a_zip_and_a_finder_are_absent_to_load_and_broken_to_the_scan(
        tmp_path, monkeypatch, fresh):
    import zipfile
    from importlib.metadata import Distribution

    root = tmp_path / "site"
    write_dist(root, "moxi_hpplain-1.0.dist-info", name="moxi-hpplain", version="1.0",
               entries={"hpplain": "moxi_hpplain.module"})
    write_package(root, "moxi_hpplain")
    egg = root / "moxi_hpegg-1.0.egg-info"
    egg.mkdir()
    (egg / "PKG-INFO").write_text(metadata_text("moxi-hpegg", "1.0"))
    (egg / "entry_points.txt").write_text(entry_points_text({"hpegg": "moxi_hpegg.module"}))
    write_package(root, "moxi_hpegg")
    archive = tmp_path / "hpzip.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("moxi_hpzip-1.0.dist-info/METADATA", metadata_text("moxi-hpzip", "1.0"))
        z.writestr("moxi_hpzip-1.0.dist-info/entry_points.txt",
                   entry_points_text({"hpzip": "moxi_hpzip.module"}))
        z.writestr("moxi_hpzip/__init__.py", "")
        z.writestr("moxi_hpzip/module.py", "SUMMARY = 'A module made for a test.'\n")
    monkeypatch.syspath_prepend(str(archive))
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.setattr(sys, "meta_path",
                        [*sys.meta_path, _finder("moxi-hpfinder",
                                                 {"hpfinder": "moxi_hpfinder.module"})])

    # The full scan sees only this test's distributions.
    ours = {"moxi-hpplain", "moxi-hpegg", "moxi-hpzip", "moxi-hpfinder"}
    discover = Distribution.discover

    def only_ours(cls, **kwargs):
        return (d for d in discover(**kwargs) if d.metadata["Name"] in ours)

    monkeypatch.setattr(Distribution, "discover", classmethod(only_ours))

    for name in ("hpegg", "hpzip", "hpfinder"):
        assert reference(name) is not None, name
        assert fast(name) is None, name
        found = modules.load(name)
        assert found.state == ABSENT, name
        assert f"moxi-{name} is not installed" in found.reason
    assert modules.load("hpplain").state == PRESENT

    scanned = {f.name: f for f in modules.scan()}
    assert sorted(scanned) == ["hpegg", "hpfinder", "hpplain", "hpzip"]
    assert scanned["hpplain"].state == PRESENT
    for name in ("hpegg", "hpzip", "hpfinder"):
        assert (scanned[name].state, scanned[name].reason) == (BROKEN, REINSTALL), name
        assert scanned[name].distribution == f"moxi-{name}"


# --- the record types are plain frozen classes --------------------------------------------

def test_found_compares_hashes_and_prints_as_a_frozen_dataclass_did():
    a = modules.Found(name="x", state=ABSENT, reason="gone")
    b = modules.Found("x", ABSENT, "gone")
    assert a == b
    with pytest.raises(TypeError):  # its tables are read-only dicts, which do not hash
        hash(a)
    assert a != a._replace(reason="other")
    assert a._replace(api=sys) == a  # the API module is left out of equality and repr
    assert "api=" not in repr(a) and "api_name=None" in repr(a)
    assert repr(a).startswith("Found(name='x', state='absent', reason='gone', "
                              "distribution=None, ")
    assert a.optional == {} and a.reserved_names == () and a.announces == 1
    assert modules.Found._fields[:3] == ("name", "state", "reason")
    with pytest.raises(AttributeError):
        a.state = PRESENT
    with pytest.raises(AttributeError):
        a.extra = 1
    with pytest.raises(TypeError):
        modules.Found(name="x")
    with pytest.raises(TypeError):
        modules.Found(name="x", state=ABSENT, nonesuch=1)
    with pytest.raises(TypeError):
        a._replace(nonesuch=1)


def test_entry_and_extension_are_plain_frozen_classes():
    import copy
    import pickle

    entry = modules.Entry("alpha", "moxi-alpha", "1.0", "moxi_alpha.module")
    assert entry == modules.Entry(name="alpha", distribution="moxi-alpha", version="1.0",
                                  target="moxi_alpha.module")
    assert repr(entry) == ("Entry(name='alpha', distribution='moxi-alpha', version='1.0', "
                           "target='moxi_alpha.module')")
    assert pickle.loads(pickle.dumps(entry)) == entry and copy.copy(entry) == entry
    ext = modules.Extension("comms", "comms.kinds", "moxi_comms.kinds", "moxi-comms")
    assert {ext, ext._replace()} == {ext}
    assert ext != entry
    with pytest.raises(AttributeError):
        ext.target = "other"
    assert not hasattr(ext, "__dict__")


# --- the hook path loads nothing heavy ----------------------------------------------------

def test_the_hook_path_loads_none_of_the_heavy_modules(tmp_path):
    write_dist(tmp_path, "moxi_hpone-0.1.0.dist-info", name="moxi-hpone", version="0.1.0",
               entries={"hpone": "moxi_hpone.module"})
    write_package(tmp_path, "moxi_hpone")
    code = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(tmp_path)!r})
        import moxi.paths
        import moxi.modules as m
        states = []
        for name in ("hpone", "hpnosuchmodule"):
            m.available(name)
            states.append(m.load(name).state)
        print(*states)
        print(*[name for name in {HEAVY!r} if name in sys.modules])
    """)
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                         text=True, check=True)
    states, heavy = (out.stdout.splitlines() + ["", ""])[:2]
    assert states == f"{PRESENT} {ABSENT}"
    assert heavy == "", f"the hook path loaded {heavy}"
