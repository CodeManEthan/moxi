"""The vendored libraries: what `vendor.txt` lists is what `_vendor/` holds, imported only
through `moxi._vendor`, and the tool that makes the copies."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import build_wheel

TOOL = Path(__file__).resolve().parents[1] / "tools" / "vendor.py"
VENDOR = Path(__file__).resolve().parents[1] / "src" / "moxi" / "_vendor"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("vendor_tool", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_each_listed_library_is_vendored_at_its_version_with_its_licence(tool):
    pairs = dict(tool.listed())
    assert set(pairs) == {"tomlkit", "packaging"}
    assert {p.name for p in VENDOR.iterdir() if p.is_dir() and p.name != "__pycache__"} \
        == set(pairs)
    from moxi._vendor import packaging, tomlkit

    assert tomlkit.__version__ == pairs["tomlkit"]
    assert packaging.__version__ == pairs["packaging"]
    for name in pairs:
        assert (VENDOR / name / "LICENSE").is_file()


def test_no_vendored_file_imports_an_installed_copy():
    bad = []
    for path in VENDOR.rglob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.match(r"\s*(from|import) (tomlkit|packaging)\b", line):
                bad.append(f"{path.relative_to(VENDOR)}:{number}: {line.strip()}")
    assert bad == []


def test_the_vendored_libraries_load_without_an_installed_one():
    code = ("import sys\n"
            "sys.modules['tomlkit'] = sys.modules['packaging'] = None\n"
            "from moxi._vendor import tomlkit\n"
            "from moxi._vendor.packaging.requirements import Requirement\n"
            "doc = tomlkit.parse('a = 1 # kept\\n')\n"
            "doc['a'] = 2\n"
            "print(tomlkit.dumps(doc), Requirement('x>=1').name)\n")
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                          text=True, check=True)
    assert done.stdout.split() == ["a", "=", "2", "#", "kept", "x"]


def test_the_tool_copies_the_package_and_licences_and_rewrites_self_imports(tool, tmp_path):
    wheel = build_wheel(tmp_path / "wheels", "demo", "1.2", {
        "demo/__init__.py": "from demo.core import thing\nfrom . import core\n",
        "demo/core.py": "    from demo import other  # inside a block\nthing = 1\n",
        "demo/py.typed": "",
        "demo-1.2.dist-info/licenses/LICENSE": "the licence",
    })
    dest = tmp_path / "out" / "demo"
    written = tool.unpack(wheel.read_bytes(), "demo", "1.2", dest)
    assert sorted(written) == ["LICENSE", "__init__.py", "core.py", "py.typed"]
    assert (dest / "__init__.py").read_text() == (
        "from moxi._vendor.demo.core import thing\nfrom . import core\n")
    assert (dest / "core.py").read_text().startswith(
        "    from moxi._vendor.demo import other")
    assert (dest / "LICENSE").read_text() == "the licence"


def test_the_tool_refuses_an_import_it_cannot_rewrite(tool, tmp_path):
    wheel = build_wheel(tmp_path / "wheels", "demo", "1.2",
                        {"demo/__init__.py": "import demo.core\n"})
    with pytest.raises(SystemExit, match="cannot rewrite"):
        tool.unpack(wheel.read_bytes(), "demo", "1.2", tmp_path / "out")


def test_the_tool_reads_a_listing(tool, tmp_path):
    listing = tmp_path / "vendor.txt"
    listing.write_text("# comment\ntomlkit==0.15.2\n\npackaging==26.3  # why\n")
    assert tool.listed(listing) == [("tomlkit", "0.15.2"), ("packaging", "26.3")]
    listing.write_text("tomlkit>=1\n")
    with pytest.raises(SystemExit, match="name==version"):
        tool.listed(listing)

