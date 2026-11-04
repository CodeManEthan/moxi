"""The module contract check, rule by rule, over small repos written for each test.

The import rules import-linter and deptry check are proved in test_lint.py; here, the
names an import takes, which those tools cannot see.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from moxi_dev import contract
from moxi_dev.contract import CheckError, check
from moxi_dev.testing import need_symlinks

ANNOUNCEMENT = {
    "INTERFACES": "frozenset({1})",
    "API": "None",
    "CLI": "None",
    "SUMMARY": "'A module made for a test.'",
    "OPTIONAL": "{}",
    "GATED": "{}",
    "INTERNAL_ENV": "{}",
    "SETTINGS": "None",
    "SETUP": "None",
    "SEGMENT": "None",
}


def announcement(drop=(), **values) -> str:
    fields = {**ANNOUNCEMENT, **values}
    return "".join(f"{k} = {v}\n" for k, v in fields.items() if k not in drop)


def pyproject(name="moxi-alpha", *, deps=("moxi>=0.1",), extras=None, groups=None,
              scripts=None, entry_points=None, tool="") -> str:
    lines = ["[project]", f'name = "{name}"', 'version = "0.1.0"',
             "dependencies = [" + ", ".join(f'"{d}"' for d in deps) + "]"]
    if extras:
        lines.append("[project.optional-dependencies]")
        lines += [f"{k} = [" + ", ".join(f'"{r}"' for r in v) + "]" for k, v in extras.items()]
    if scripts is None:
        # Only the core declares the moxi script.
        scripts = {"moxi": "moxi.cli:main"} if name == "moxi" else {}
    if scripts:
        lines.append("[project.scripts]")
        lines += [f'"{k}" = "{v}"' for k, v in scripts.items()]
    if entry_points is None:
        module = name.removeprefix("moxi-")
        entry_points = {module: f"moxi_{module}.module"}
    if entry_points:
        lines.append('[project.entry-points."moxi.modules"]')
        lines += [f'"{k}" = "{v}"' for k, v in entry_points.items()]
    groups = {"dev": ["pytest"]} if groups is None else groups
    if groups:
        lines.append("[dependency-groups]")
        lines += [f"{k} = [" + ", ".join(f'"{r}"' for r in v) + "]" for k, v in groups.items()]
    return "\n".join(lines) + "\n" + textwrap.dedent(tool)


class Repo:
    """A module repo on disk, alpha unless named otherwise."""

    def __init__(self, root: Path, name: str = "alpha"):
        self.root = root
        self.name = name
        self.pkg = f"moxi_{name}"

    def write(self, rel: str, text: str = "") -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        # The text's own bytes, on every system: no locale encoding, no CRLF added.
        path.write_text(textwrap.dedent(text), encoding="utf-8", newline="")
        return path

    def src(self, rel: str, text: str = "") -> Path:
        return self.write(f"src/{self.pkg}/{rel}", text)

    def findings(self, which: str | None = None, *, staged: bool = False) -> list[str]:
        found = check(self.root, staged=staged)
        return [f"{f.path}:{f.line}: {f.message}" for f in found
                if which is None or f.check == which]

    def git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.root), *args], check=True,
                       capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """A module repo that keeps the contract: alpha, requiring the core and beta."""
    beta = Repo(tmp_path / "beta", "beta")
    beta.write("pyproject.toml", pyproject("moxi-beta"))
    beta.src("__init__.py")
    beta.src("api.py", "def send(): pass\n")
    beta.src("module.py", announcement(API="'moxi_beta.api'"))
    r = Repo(tmp_path / "alpha")
    r.write("pyproject.toml", pyproject(deps=("moxi>=0.1", "moxi-beta>=0.1")))
    r.src("__init__.py")
    r.src("module.py", announcement())
    r.src("core.py", """\
        import os
        from moxi import names
        from moxi.paths import Layout
        import moxi.config
        from moxi_beta.api import send
        """)
    r.write("tests/test_core.py", "import pytest\nimport moxi_alpha.core\n")
    return r


def test_a_repo_that_keeps_the_contract_has_no_findings(repo):
    assert repo.findings() == []


def test_no_pyproject_is_an_error(tmp_path):
    with pytest.raises(CheckError, match="no pyproject.toml"):
        check(tmp_path)


def test_a_finding_reads_as_path_line_check_message():
    f = contract.Finding("src/a.py", 3, "2", "imports x")
    assert str(f) == "src/a.py:3: check 2: imports x"
    assert str(contract.Finding("pyproject.toml", 0, "7", "m")) == "pyproject.toml: check 7: m"


def test_a_file_that_does_not_parse_is_a_finding(repo):
    repo.src("broken.py", "def (:\n")
    assert any("does not parse" in m for m in repo.findings("read"))


# --- check 1: the names an import takes ------------------------------------------------------

def test_a_private_name_from_an_api_is_refused(repo):
    repo.src("core.py", "from moxi_beta.api import _secret\n")
    assert "a name starting with _" in repo.findings("1")[0]


def test_the_core_modules_another_module_may_import(repo):
    shared = ("names", "paths", "config", "modules", "cli", "files", "store", "proc", "log",
              "exit", "aio", "ipc", "units", "doctor", "place")
    assert contract.CORE_MODULES == frozenset(shared)
    repo.src("core.py", f"from moxi import {', '.join(shared)}\nfrom moxi.doctor import Row\n")
    assert repo.findings("1") == []
    own = ("install", "uninstall", "run", "catalogue", "strays", "home")
    repo.src("core.py", f"from moxi import {', '.join(own)}\n")
    found = repo.findings("1")
    assert len(found) == len(own), found


def test_a_private_name_in_tests_is_refused_too(repo):
    repo.write("tests/test_core.py", "from moxi_beta.api import _secret\n")
    assert repo.findings("1")[0].startswith("tests/test_core.py:1:")


def test_a_private_module_of_a_required_module_is_refused(repo):
    repo.src("core.py", "import moxi_beta._wire\n")
    assert repo.findings("1") == [
        "src/moxi_alpha/core.py:1: imports moxi_beta._wire: a name starting with _ is not "
        "part of an API"]


def test_a_name_from_an_undeclared_package_is_left_to_the_lint(repo):
    repo.src("core.py", "from moxi_gamma.api import _secret\nfrom other import _x\n")
    assert repo.findings("1") == []


@pytest.mark.parametrize("line", [
    "from moxi.paths import _expanduser",
    "import moxi._private",
    "from moxi import _thing",
    "from moxi import secret",
])
def test_core_names_outside_the_table_are_refused(repo, line):
    repo.src("core.py", line + "\n")
    assert len(repo.findings("1")) == 1


@pytest.mark.parametrize("line", ["import moxi", "from moxi import names, paths, config",
                                  "import moxi.modules", "from moxi.config import Schema",
                                  "import moxi.cli", "from moxi.cli import main",
                                  "from moxi import *"])
def test_core_names_in_the_table_pass(repo, line):
    repo.src("core.py", line + "\n")
    assert repo.findings("1") == []


def test_the_contract_is_no_longer_a_core_module(repo):
    repo.src("core.py", "from moxi import contract\n")
    assert "imports moxi.contract, which is not one of the core's modules" in (
        repo.findings("1")[0])


def core_repo(root: Path) -> Repo:
    r = Repo(root, "core")
    r.pkg = "moxi"
    r.write("pyproject.toml", pyproject("moxi", deps=(), entry_points={}))
    r.src("__init__.py")
    r.src("cli.py", "def main(argv=None):\n    return 0\n")
    return r


def test_the_core_reaches_its_own_internals(tmp_path):
    r = core_repo(tmp_path / "moxi")
    r.src("config.py", "from .paths import _expanduser\nfrom moxi.paths import _root\n")
    r.src("paths.py", "def _expanduser(): pass\ndef _root(): pass\n")
    assert r.findings() == []


def test_the_contributors_package_is_no_module(tmp_path):
    """A package's tests import moxi_dev like any declared package, and moxi-dev itself
    is held to neither check 1 nor check 7."""
    r = core_repo(tmp_path / "moxi")
    r.write("pyproject.toml", pyproject("moxi", deps=(), entry_points={},
                                        groups={"dev": ["pytest", "moxi-dev"]}))
    r.write("tests/test_contract.py", "from moxi_dev.contract import check\n"
                                      "from moxi_dev import _anything\n")
    assert r.findings() == []
    dev = Repo(tmp_path / "moxi-dev", "dev")
    dev.write("pyproject.toml", pyproject("moxi-dev", entry_points={},
                                          scripts={"moxi-dev": "moxi_dev.cli:main"}))
    dev.src("__init__.py")
    dev.src("cli.py", "from moxi import _private\nimport moxi_beta._x\n"
                      "def main(argv=None):\n    return 0\n")
    assert dev.findings() == []


def test_the_contributors_package_reads_its_own_variables(tmp_path):
    dev = Repo(tmp_path / "moxi-dev", "dev")
    dev.write("pyproject.toml", pyproject("moxi-dev", entry_points={}, scripts={}))
    dev.src("__init__.py", """\
        import os
        LIVE = os.environ.get("MOXI_TEST_LIVE")
        OTHER = os.environ.get("MOXI_DEV_MODE")
        """)
    found = dev.findings("5")
    assert len(found) == 1 and "reads MOXI_DEV_MODE" in found[0]


# --- check 3 ---------------------------------------------------------------------------------

@pytest.fixture
def extra_repo(repo):
    repo.write("pyproject.toml", pyproject(
        deps=("moxi", "moxi-beta"), extras={"codex": ["websockets>=17"]},
        scripts={"moxi-alpha-codex": "moxi_alpha.codex:main",
                 "moxi-alpha-daemon": "moxi_alpha.daemon:main"}))
    repo.src("module.py", announcement(OPTIONAL="{'codex': 'The Codex host.'}",
                                       GATED="{'moxi-alpha-codex': 'codex'}",
                                       CLI="'moxi_alpha.cli:main'"))
    repo.src("cli.py", "def main(argv=None, *, prog='moxi alpha'):\n    return 0\n")
    repo.src("daemon.py", "def main(): pass\n")
    repo.src("adapters/__init__.py")
    repo.src("adapters/ws.py", "import websockets\n")
    repo.src("codex.py", "from .adapters import ws\ndef main(): pass\n")
    return repo


def test_an_extra_behind_a_gated_script_passes(extra_repo):
    assert extra_repo.findings() == []


def test_an_extra_reached_from_an_ungated_script_is_refused(extra_repo):
    extra_repo.src("daemon.py", "from moxi_alpha.adapters.ws import x\ndef main(): pass\n")
    found = extra_repo.findings("3")
    assert len(found) == 1
    assert found[0].startswith("src/moxi_alpha/adapters/ws.py:1:")
    assert "the console script moxi-alpha-daemon" in found[0]


def test_an_extra_reached_from_the_cli_target_through_a_chain_is_refused(extra_repo):
    extra_repo.src("helper.py", "from .adapters.ws import thing\n")
    extra_repo.src("cli.py", "from . import helper\n"
                             "def main(argv=None, *, prog='moxi alpha'):\n    return 0\n")
    assert "the CLI target" in extra_repo.findings("3")[0]


def test_an_extra_in_a_package_init_reached_by_a_submodule_is_refused(extra_repo):
    extra_repo.src("adapters/__init__.py", "import websockets\n")
    extra_repo.src("api.py", "from moxi_alpha.adapters import other\n")
    extra_repo.src("adapters/other.py", "")
    extra_repo.src("module.py", announcement(OPTIONAL="{'codex': 'The Codex host.'}",
                                             GATED="{'moxi-alpha-codex': 'codex'}",
                                             CLI="'moxi_alpha.cli:main'",
                                             API="'moxi_alpha.api'"))
    assert "the API module" in extra_repo.findings("3")[0]


def test_an_extra_imported_inside_a_function_or_for_typing_passes(extra_repo):
    extra_repo.src("daemon.py", """\
        from typing import TYPE_CHECKING
        if TYPE_CHECKING:
            import websockets
        def main():
            import websockets
        """)
    assert extra_repo.findings() == []


# --- check 4 ---------------------------------------------------------------------------------

def test_an_extra_without_a_sentence_and_a_sentence_without_an_extra(repo):
    repo.write("pyproject.toml", pyproject(deps=("moxi", "moxi-beta"),
                                           extras={"fast": ["orjson"]}))
    repo.src("module.py", announcement(OPTIONAL="{'slow': 'Slower.'}"))
    found = repo.findings("4")
    assert len(found) == 2
    assert "declares the extra 'fast' with no OPTIONAL sentence" in found[0] + found[1]
    assert "OPTIONAL names 'slow'" in found[0] + found[1]


def test_an_extra_installing_a_module_is_named_by_it(repo):
    repo.write("pyproject.toml", pyproject(deps=("moxi",),
                                           extras={"with-beta": ["moxi-beta>=0.1"]}))
    repo.src("module.py", announcement(OPTIONAL="{'with-beta': 'Beta.'}"))
    repo.src("core.py", "")
    assert "is named by it: 'beta'" in repo.findings("4")[0]


def test_gated_names_must_exist(repo):
    repo.src("module.py", announcement(GATED="{'moxi-alpha-x': 'nothing'}"))
    found = repo.findings("4")
    assert len(found) == 2


def test_the_core_declares_no_extras(tmp_path):
    r = core_repo(tmp_path / "moxi")
    r.write("pyproject.toml", pyproject("moxi", deps=(), entry_points={},
                                        extras={"fast": ["orjson"]}))
    assert "the core declares no extras" in r.findings("4")[0]


# --- check 5 ---------------------------------------------------------------------------------

def test_a_moxi_key_outside_internal_env_is_refused(repo):
    repo.src("core.py", """\
        import os
        A = os.environ.get("MOXI_ALPHA_MODE")
        B = os.environ["MOXI_TURNS_COMMANDS"]
        C = os.getenv("HOME")
        D = "MOXI_ALPHA_NAME" in os.environ
        """)
    found = repo.findings("5")
    assert [f.split(":")[1] for f in found] == ["2", "3", "5"]
    assert "reads MOXI_ALPHA_MODE, which INTERNAL_ENV does not declare" in found[0]


def test_internal_env_and_its_prefixes_cover_reads(repo):
    repo.src("module.py", announcement(INTERNAL_ENV="{'MOXI_ALPHA_NAME': 'stamped', "
                                                    "'MOXI_ALPHA_SECRET_': 'keys'}"))
    repo.src("core.py", """\
        import os
        from os import environ, getenv
        PREFIX = "MOXI_ALPHA_SECRET_"
        A = os.environ.get("MOXI_ALPHA_NAME")
        B = environ["MOXI_ALPHA_SECRET_GATEWAY"]
        def secret(role):
            return getenv(f"MOXI_ALPHA_SECRET_{role}") or os.environ.get(PREFIX + role)
        """)
    assert repo.findings() == []


def test_a_computed_key_is_refused_and_a_constant_is_read(repo):
    repo.src("core.py", """\
        import os as o
        KEY = "MOXI_ALPHA_MODE"
        OTHER = "EDITOR"
        def f(name):
            o.environ.get(name)
            o.environ.get(KEY)
            o.environ.get(OTHER)
            o.environ.get(f"MOXI_ALPHA_{name}")
        """)
    found = repo.findings("5")
    assert [f.split(":")[1] for f in found] == ["5", "6", "8"]
    assert "neither a literal nor a module-level string constant" in found[0]
    assert "reads MOXI_ALPHA_MODE" in found[1]


def test_writes_and_tests_are_not_reads_checked(repo):
    repo.src("core.py", "import os\nos.environ['MOXI_ALPHA_X'] = '1'\n"
                        "env = dict(os.environ)\n")
    repo.write("tests/test_core.py", "import os\nos.environ.get('MOXI_ALPHA_X')\n")
    assert repo.findings("5") == []


def test_the_core_reads_computed_keys_only_in_paths_and_config(tmp_path):
    r = core_repo(tmp_path / "moxi")
    r.src("paths.py", "import os\ndef root(p, s):\n    return os.environ.get(f'{p}{s}')\n")
    r.src("config.py", "import os\ndef env(k):\n    return os.environ.get(k)\n")
    assert r.findings() == []
    r.src("names.py", "import os\ndef env(k):\n    return os.environ.get(k)\n")
    assert len(r.findings("5")) == 1
    r.src("names.py", "import os\nos.environ.get('MOXI_X')\n")
    assert "reads MOXI_X" in r.findings("5")[0]


# --- check 6 ---------------------------------------------------------------------------------

def test_its_own_script_looked_up_on_path_is_refused(extra_repo):
    extra_repo.src("daemon.py", """\
        import shutil
        from shutil import which as w
        DAEMON = "moxi-alpha-daemon"
        def main():
            shutil.which("git")
            shutil.which(DAEMON)
            w("moxi-alpha-codex")
        """)
    found = extra_repo.findings("6")
    assert [f.split(":")[1] for f in found] == ["6", "7"]
    assert "moxi.paths.script" in found[0]


def test_the_core_looks_moxi_up_only_in_its_cli(tmp_path):
    r = core_repo(tmp_path / "moxi")
    r.src("cli.py", "import shutil\ndef main(argv=None):\n"
                    "    return shutil.which('moxi'), shutil.which(argv[0])\n")
    assert r.findings() == []
    r.src("paths.py", "import shutil\nWHERE = shutil.which('moxi')\n")
    assert r.findings("6")[0].startswith("src/moxi/paths.py:2:")


# --- check 7 ---------------------------------------------------------------------------------

def test_a_distribution_not_spelled_moxi_name(repo):
    repo.write("pyproject.toml", pyproject("alpha-tools", deps=("moxi", "moxi-beta"),
                                           entry_points={"alpha": "moxi_alpha.module"}))
    assert any("names the distribution 'alpha-tools'" in m for m in repo.findings("7"))


def test_a_module_named_by_a_core_verb(tmp_path):
    r = Repo(tmp_path / "config", "config")
    r.write("pyproject.toml", pyproject("moxi-config"))
    r.src("__init__.py")
    r.src("module.py", announcement())
    assert "one of the umbrella's own verbs" in r.findings("7")[0]


@pytest.mark.parametrize("eps, needle", [
    ({}, "declares no"),
    ({"other": "moxi_alpha.module"}, "it is named by the module: 'alpha'"),
    ({"alpha": "moxi_alpha.module:thing"}, "with no attribute"),
    ({"alpha": "moxi_alpha.nowhere"}, "which is no module under src/"),
    ({"alpha": "moxi_alpha.module", "beta": "moxi_alpha.module"}, "declares 2"),
])
def test_the_entry_point(repo, eps, needle):
    repo.write("pyproject.toml", pyproject(deps=("moxi", "moxi-beta"), entry_points=eps))
    assert any(needle in m for m in repo.findings("7")), repo.findings("7")


def test_console_scripts_are_named_moxi_module_part(repo):
    repo.write("pyproject.toml", pyproject(deps=("moxi", "moxi-beta"), scripts={
        "moxi-alpha-daemon": "moxi_alpha.d:main",
        "alpha-daemon": "moxi_alpha.d:main", "moxi-alpha-": "moxi_alpha.d:main",
        "moxi-beta-x": "moxi_alpha.d:main"}))
    assert len(repo.findings("7")) == 3


@pytest.mark.parametrize("target", ["moxi.cli:main", "moxi_alpha.module:main"])
def test_a_module_never_declares_the_moxi_script(repo, target):
    """Only the core declares moxi, whatever a module's line points at."""
    repo.write("pyproject.toml", pyproject(deps=("moxi", "moxi-beta"), scripts={
        "moxi": target}))
    assert repo.findings("7") == [
        "pyproject.toml:0: declares the console script moxi; only the core declares it, "
        "and a module is installed with `moxi install alpha`"]


def test_a_second_import_package(repo):
    repo.write("src/helpers/__init__.py", "")
    assert repo.findings("7") == ["src/helpers:0: is an import package other than moxi_alpha"]


def test_the_announcement_defines_every_name_as_a_literal(repo):
    repo.src("module.py", announcement(drop=("SEGMENT",), API="make()",
                                       SUMMARY="'two\\nlines'"))
    found = repo.findings("7")
    assert not any("SEGMENT" in m for m in found)
    assert any("defines API by an expression" in m for m in found)
    assert any("one non-empty line" in m for m in found)


def test_the_checker_reads_the_one_list_of_names():
    from moxi import modules

    assert contract.KNOWN is modules.KNOWN
    assert not hasattr(contract, "ANNOUNCEMENT")


def test_an_announcement_may_leave_any_name_out(repo):
    repo.src("module.py", "")
    assert repo.findings("7") == []


def test_every_new_name_present_is_held_to_load_s_checks(repo):
    repo.src("module.py", announcement(
        DAEMONS="{'relay': {'command': 'moxi-alpha-relay', 'restart': 'always'}}",
        TIMERS="{'sweep': {'command': 'moxi-alpha-sweep', 'every': '5m'}}",
        BUDGETS="{'import_ms': 2}",
        PAGE="'moxi_alpha.page'",
        RESERVED_NAMES="['bot', 'bot']",
        SESSION_ENV="{'MOXI_ALPHA_NAME': 'stamped'}"))
    found = repo.findings("7")
    path = "src/moxi_alpha/module.py:0: the announcement has "
    assert all(m.startswith(path) for m in found), found
    for words in ("key 'restart', which this moxi does not know", "no switch",
                  "BUDGETS key 'import_ms'", "PAGE", "no name twice",
                  "own prefix MOXI_ALPHA_, and INTERNAL_ENV does not declare it"):
        assert any(words in m for m in found), (words, found)


def test_every_new_name_that_keeps_the_contract_passes(repo):
    repo.src("module.py", announcement(
        ANNOUNCES="1",
        DAEMONS="{'relay': {'command': 'moxi-alpha-relay', 'stop': 'process', "
                "'socket': {'setting': 'port', 'addresses': ['127.0.0.1']}}}",
        TIMERS="{'sweep': {'command': 'moxi-alpha-sweep', 'at': ['09:00'], "
               "'days': ['mon'], 'switch': 'sweep.enabled'}}",
        HEALTH="{'relay': 'moxi_alpha.health:relay'}",
        STORES="dict(record={'root': 'state', 'path': 'record.db', 'kind': 'sqlite'})",
        BUDGETS="{'idle_cpu': 0.01, 'rss': '150M'}",
        SKILLS="'skills'", COMPONENTS="{'panel': {}}", TEARDOWN="'moxi_alpha.s:down'",
        SESSION_ENV="{'MOXI_ALPHA_NAME': 'stamped'}",
        INTERNAL_ENV="{'MOXI_ALPHA_NAME': 'stamped'}",
        QUESTIONS="{'sweep.enabled': 'Sweep each morning?'}"))
    repo.src("skills/moxi-alpha-sweep/SKILL.md", "---\nname: moxi-alpha-sweep\n---\n")
    assert repo.findings("7") == []


def test_a_newer_schema_is_one_finding(repo):
    repo.src("module.py", announcement(ANNOUNCES="3", DAEMONS="{'x': {'later': 1}}"))
    assert repo.findings("7") == [
        "src/moxi_alpha/module.py:0: the announcement announces schema 3; this moxi "
        "knows up to 2; upgrade moxi"]


@pytest.mark.parametrize("name", ["installs", "runtime", "set", "unset"])
def test_a_module_named_by_a_name_the_core_keeps(tmp_path, name):
    r = Repo(tmp_path / name, name)
    r.write("pyproject.toml", pyproject(f"moxi-{name}"))
    r.src("__init__.py")
    r.src("module.py", announcement())
    assert "a name the core keeps for itself" in r.findings("7")[0]


def test_a_module_named_by_a_new_verb(tmp_path):
    r = Repo(tmp_path / "doctor", "doctor")
    r.write("pyproject.toml", pyproject("moxi-doctor"))
    r.src("__init__.py")
    r.src("module.py", announcement())
    assert "one of the umbrella's own verbs" in r.findings("7")[0]


def test_the_cli_target_takes_argv_and_prog(repo):
    repo.src("module.py", announcement(CLI="'moxi_alpha.cli:main'"))
    repo.src("cli.py", "def main(argv=None, ctx=None):\n    return 0\n")
    assert repo.findings("7") == [
        "src/moxi_alpha/cli.py:1: the CLI target main lacks prog; `moxi <module>` calls it "
        "as main(argv, prog=\"moxi <module>\")"]
    repo.src("cli.py", "def main(argv=None, *, prog='moxi alpha'):\n    return 0\n")
    assert repo.findings() == []
    repo.src("cli.py", "")
    assert "no top-level function main" in repo.findings("7")[0]


def test_the_settings_schema_parses(repo):
    repo.src("module.py", announcement(SETTINGS="'settings.toml'"))
    assert "does not exist" in repo.findings("7")[0]
    repo.src("settings.toml", '[mode]\ntype = "string"\ndefault = "a"\nread = "start"\n')
    assert "lacks doc" in repo.findings("7")[0]
    repo.src("settings.toml", '[mode]\ntype = "string"\ndefault = "a"\nread = "start"\n'
                              'doc = "The mode."\nenv = "OTHER_MODE"\n')
    assert "prefix MOXI_ALPHA_" in repo.findings("7")[0]
    repo.src("settings.toml", '[mode]\ntype = "string"\ndefault = "a"\nread = "start"\n'
                              'doc = "The mode."\nenv = "MOXI_ALPHA_MODE"\n')
    assert repo.findings() == []


def test_the_core_is_held_to_its_own_names(tmp_path):
    r = core_repo(tmp_path / "moxi")
    assert r.findings() == []
    r.write("pyproject.toml", pyproject("moxi", deps=(), entry_points={"moxi": "moxi.mod"},
                                        scripts={"moxi": "moxi.other:main", "mx": "moxi.x:y"}))
    found = r.findings("7")
    assert len(found) == 3
    assert any("the core is not a module" in m for m in found)
    r.write("pyproject.toml", pyproject("moxi", deps=(), entry_points={}, scripts={}))
    assert "does not declare the console script moxi" in r.findings("7")[0]


# --- the skills ------------------------------------------------------------------------------

SKILL_RULE = ("is not a Moxi skill name: a skill Moxi ships is named moxi-<name>, lower-case "
              "ASCII letters and digits in words joined by single hyphens, 64 characters at "
              "most")


def skill(repo, name, text=None, *, folder="skills"):
    body = f"---\nname: {name}\ndescription: A skill.\n---\n\n# {name}\n" if text is None \
        else text
    return repo.src(f"{folder}/{name}/SKILL.md", body)


@pytest.fixture
def skilled(repo):
    """alpha declaring `SKILLS = 'skills'`, with one skill that keeps the rule."""
    repo.src("module.py", announcement(SKILLS="'skills'"))
    skill(repo, "moxi-handoff")
    return repo


def test_a_skill_named_moxi_name_with_its_name_line_passes(skilled):
    assert skilled.findings() == []
    skill(skilled, "moxi-handoff", "---\nname: 'moxi-handoff'\n---\n")
    assert skilled.findings() == []
    skill(skilled, "moxi-handoff", '---\nname:   "moxi-handoff"  \ndescription: x\n---\n')
    assert skilled.findings() == []


def test_dot_folders_pycache_and_plain_files_in_skills_are_not_skills(skilled):
    skilled.src("skills/__pycache__/x.pyc", "")
    skilled.src("skills/.x/notes.txt", "")
    skilled.src("skills/README.md", "the skills\n")
    skilled.src("skills/moxi-handoff/scripts/run.sh", "")
    assert skilled.findings() == []


def test_a_skill_whose_folder_is_not_a_skill_name_fails_naming_it(skilled):
    skill(skilled, "handoff")
    assert skilled.findings("7") == [
        f"src/moxi_alpha/skills/handoff:0: is a skill folder, and 'handoff' {SKILL_RULE}"]


def test_a_skill_with_no_skill_md_or_another_name_fails_naming_it(skilled):
    skill(skilled, "moxi-handoff", "---\nname: handoff\n---\n")
    assert skilled.findings("7") == [
        "src/moxi_alpha/skills/moxi-handoff/SKILL.md:2: names the skill 'handoff'; its "
        "name: is the folder's name, moxi-handoff"]
    (skilled.root / "src/moxi_alpha/skills/moxi-handoff/SKILL.md").unlink()
    skilled.src("skills/moxi-handoff/notes.md", "")
    assert skilled.findings("7") == [
        "src/moxi_alpha/skills/moxi-handoff:0: is a skill folder with no SKILL.md"]


def test_a_skills_folder_declared_and_missing_or_a_file_fails(repo):
    repo.src("module.py", announcement(SKILLS="'skills'"))
    assert repo.findings("7") == [
        "src/moxi_alpha/module.py:0: SKILLS names 'skills', and src/moxi_alpha/skills does "
        "not exist"]
    repo.src("skills", "not a folder\n")
    assert repo.findings("7") == [
        "src/moxi_alpha/module.py:0: SKILLS names 'skills', and src/moxi_alpha/skills is a "
        "file, not a folder"]


def test_no_skills_declared_reads_no_skills_folder(repo):
    skill(repo, "handoff")
    assert repo.findings() == []


def test_a_skills_path_in_parts_resolves_beside_the_announcement(repo):
    repo.src("module.py", announcement(SKILLS="'./data/skills/'"))
    skill(repo, "moxi-handoff", folder="data/skills")
    assert repo.findings() == []
    skill(repo, "bad", folder="data/skills")
    assert [f.split(":")[0] for f in repo.findings("7")] == ["src/moxi_alpha/data/skills/bad"]


def test_a_skills_path_with_a_backslash_fails(repo):
    # importlib.resources reads data\skills as one name on POSIX, so the install would
    # look for a folder that isn't there.
    repo.src("module.py", announcement(SKILLS="'./data\\\\skills/'"))
    skill(repo, "moxi-handoff", folder="data/skills")
    assert repo.findings("7") == [
        "src/moxi_alpha/module.py:0: SKILLS names './data\\\\skills/', which has a "
        "backslash; write the path with /, which resolves the same on every platform"]


def test_the_skills_folder_is_in_the_package_that_holds_the_announcement(repo):
    """As `importlib.resources` reads it: beside an announcement module, in the package it
    is part of, and inside an announcement that is a package itself."""
    repo.write("pyproject.toml", pyproject(deps=("moxi>=0.1", "moxi-beta>=0.1"),
                                           entry_points={"alpha": "moxi_alpha.ann.module"}))
    repo.src("ann/__init__.py")
    repo.src("ann/module.py", announcement(SKILLS="'skills'"))
    skill(repo, "moxi-handoff")                  # src/moxi_alpha/skills: not the one read
    assert repo.findings("7") == [
        "src/moxi_alpha/ann/module.py:0: SKILLS names 'skills', and "
        "src/moxi_alpha/ann/skills does not exist"]
    skill(repo, "moxi-handoff", folder="ann/skills")
    assert repo.findings() == []
    repo.write("pyproject.toml", pyproject(deps=("moxi>=0.1", "moxi-beta>=0.1"),
                                           entry_points={"alpha": "moxi_alpha.ann"}))
    repo.src("ann/__init__.py", announcement(SKILLS="'skills'"))
    assert repo.findings() == []
    skill(repo, "handoff", folder="ann/skills")
    assert [f.split(":")[0] for f in repo.findings("7")] == [
        "src/moxi_alpha/ann/skills/handoff"]


@pytest.mark.parametrize("text", [
    "﻿---\nname: moxi-handoff\n---\n",
    "---\r\nname: moxi-handoff\r\ndescription: x\r\n---\r\n# body\r\n",
    "﻿---\r\nname: moxi-handoff\r\n---\r\n",
    "---  \nmetadata:\n  name: other\nname: moxi-handoff\n---\nname: after\n",
    "---\nname:moxi-handoff\n---",
])
def test_the_name_line_is_read_from_the_frontmatter_alone(skilled, text):
    skill(skilled, "moxi-handoff", text)
    assert skilled.findings() == []


@pytest.mark.parametrize("text, line, problem", [
    ("# no frontmatter\nname: moxi-handoff\n", 1, "does not open with a --- line"),
    ("\n---\nname: moxi-handoff\n---\n", 1, "does not open with a --- line"),
    ("---\nname: moxi-handoff\n", 1, "opens its frontmatter with --- and never closes it"),
    ("---\ndescription: x\n---\nname: moxi-handoff\n", 1,
     "has no name: line in its frontmatter"),
    ("---\n  name: moxi-handoff\n---\n", 1, "has no name: line in its frontmatter"),
    ("---\nname: moxi-handoff\nname: moxi-handoff\n---\n", 3,
     "has 2 name: lines in its frontmatter (lines 2, 3)"),
])
def test_a_skill_md_the_name_cannot_be_read_from_fails(skilled, text, line, problem):
    skill(skilled, "moxi-handoff", text)
    assert skilled.findings("7") == [
        f"src/moxi_alpha/skills/moxi-handoff/SKILL.md:{line}: {problem}, so it can't name "
        "the skill moxi-handoff"]


@pytest.mark.parametrize("value", ["'moxi-handoff\"", "moxi-handoff # the skill",
                                   "\"'moxi-handoff'\"", "''", "Moxi-Handoff"])
def test_a_name_written_otherwise_is_not_the_folder_name(skilled, value):
    skill(skilled, "moxi-handoff", f"---\nname: {value}\n---\n")
    found = skilled.findings("7")
    assert len(found) == 1 and "its name: is the folder's name, moxi-handoff" in found[0]


def test_a_skill_md_not_utf8_fails(skilled):
    path = skilled.root / "src/moxi_alpha/skills/moxi-handoff/SKILL.md"
    path.write_bytes(b"---\nname: moxi-\xff\n---\n")
    assert skilled.findings("7") == [
        "src/moxi_alpha/skills/moxi-handoff/SKILL.md:0: is not UTF-8, so it can't name the "
        "skill moxi-handoff"]


def test_staged_reads_the_skills_from_the_index(skilled):
    skilled.git("init", "-q")
    skilled.git("add", ".")
    assert skilled.findings(staged=True) == []
    # Folders the index has no file under are not skills there; on disk they are.
    (skilled.root / "src/moxi_alpha/skills/handoff").mkdir()
    (skilled.root / "src/moxi_alpha/skills/moxi-empty").mkdir()
    assert skilled.findings(staged=True) == []
    assert [f.split(":")[0] for f in skilled.findings("7")] == [
        "src/moxi_alpha/skills/handoff", "src/moxi_alpha/skills/handoff",
        "src/moxi_alpha/skills/moxi-empty"]
    # A staged SKILL.md naming another skill fails staged, though the disk is fixed.
    skill(skilled, "moxi-handoff", "---\nname: handoff\n---\n")
    skilled.git("add", ".")
    skill(skilled, "moxi-handoff")
    (skilled.root / "src/moxi_alpha/skills/handoff").rmdir()
    (skilled.root / "src/moxi_alpha/skills/moxi-empty").rmdir()
    assert skilled.findings() == []
    found = skilled.findings("7", staged=True)
    assert len(found) == 1 and "names the skill 'handoff'" in found[0]
    # A plain file staged in SKILLS is no skill; a misnamed skill staged is.
    skill(skilled, "moxi-handoff")
    skilled.src("skills/README.md", "the skills\n")
    skill(skilled, "handoff")
    skilled.git("add", ".")
    assert skilled.findings("7", staged=True) == [
        f"src/moxi_alpha/skills/handoff:0: is a skill folder, and 'handoff' {SKILL_RULE}"]


def test_staged_a_skills_folder_missing_or_a_file_in_the_index_fails(repo):
    repo.src("module.py", announcement(SKILLS="'skills'"))
    repo.git("init", "-q")
    repo.git("add", ".")
    assert "src/moxi_alpha/skills does not exist" in repo.findings("7", staged=True)[0]
    repo.src("skills", "not a folder\n")
    repo.git("add", ".")
    assert "is a file, not a folder" in repo.findings("7", staged=True)[0]

def symlink(path: Path, target: str) -> None:
    """A symlink at `path` whose target is `target`, as written; the test skips where
    this account may not make one (Windows without Developer Mode or the privilege)."""
    need_symlinks()
    path.symlink_to(target)


def link(repo, rel, target):
    """A symlink at src/<pkg>/<rel> whose target is `target`, as written."""
    path = repo.root / f"src/{repo.pkg}/{rel}"
    path.parent.mkdir(parents=True, exist_ok=True)
    symlink(path, target)
    return path


def staged_and_disk(repo):
    repo.git("init", "-q")
    repo.git("add", ".")
    return repo.findings("7", staged=True), repo.findings("7")


def test_a_symlinked_skill_folder_is_counted_staged_as_on_disk(skilled):
    # A misnamed link to a skill whose name: is its own folder's.
    skill(skilled, "moxi-link", folder="extra")
    link(skilled, "skills/badlink", "../extra/moxi-link")
    staged, disk = staged_and_disk(skilled)
    assert staged == disk == [
        f"src/moxi_alpha/skills/badlink:0: is a skill folder, and 'badlink' {SKILL_RULE}",
        "src/moxi_alpha/skills/badlink/SKILL.md:2: names the skill 'moxi-link'; its name: "
        "is the folder's name, badlink"]


def test_a_well_named_symlinked_skill_passes_staged_and_on_disk(skilled):
    skill(skilled, "moxi-linked", folder="extra")
    link(skilled, "skills/moxi-linked", "../extra/moxi-linked")
    assert staged_and_disk(skilled) == ([], [])


def test_a_symlinked_skill_md_is_read_staged_and_on_disk(skilled):
    skilled.src("docs/handoff.md", "---\nname: handoff\n---\n")
    (skilled.root / "src/moxi_alpha/skills/moxi-handoff/SKILL.md").unlink()
    link(skilled, "skills/moxi-handoff/SKILL.md", "../../docs/handoff.md")
    staged, disk = staged_and_disk(skilled)
    assert staged == disk == [
        "src/moxi_alpha/skills/moxi-handoff/SKILL.md:2: names the skill 'handoff'; its "
        "name: is the folder's name, moxi-handoff"]
    skilled.src("docs/handoff.md", "---\nname: moxi-handoff\n---\n")
    skilled.git("add", ".")
    assert skilled.findings(staged=True) == skilled.findings() == []


def test_a_symlinked_skills_folder_is_read_staged_and_on_disk(repo):
    repo.src("module.py", announcement(SKILLS="'skills'"))
    skill(repo, "moxi-handoff", folder="data/skills")
    skill(repo, "handoff", folder="data/skills")
    link(repo, "skills", "data/skills")
    staged, disk = staged_and_disk(repo)
    assert staged == disk == [
        f"src/moxi_alpha/skills/handoff:0: is a skill folder, and 'handoff' {SKILL_RULE}"]


def test_a_symlink_the_index_cannot_follow_fails_staged(skilled):
    skilled.git("init", "-q")
    # The target is on disk but not staged, so the commit wouldn't carry it.
    skill(skilled, "moxi-gone", folder="extra")
    link(skilled, "skills/moxi-gone", "../extra/moxi-gone")
    skilled.git("add", "src/moxi_alpha/skills", "src/moxi_alpha/module.py", "pyproject.toml")
    assert skilled.findings() == []
    assert skilled.findings("7", staged=True) == [
        "src/moxi_alpha/skills/moxi-gone:0: is a symlink whose target, ../extra/moxi-gone, "
        "isn't committed"]
    (skilled.root / "src/moxi_alpha/skills/moxi-gone").unlink()
    link(skilled, "skills/moxi-out", "../../../../elsewhere")
    link(skilled, "skills/moxi-loop", "moxi-loop")
    skilled.git("add", "-A", "src/moxi_alpha/skills")
    assert skilled.findings("7", staged=True) == [
        "src/moxi_alpha/skills/moxi-loop:0: is a symlink in a loop",
        "src/moxi_alpha/skills/moxi-out:0: is a symlink to ../../../../elsewhere, outside "
        "the package, so the commit doesn't carry what it points at"]
    # A dangling SKILLS folder is named the same way.
    (skilled.root / "src/moxi_alpha/skills").rename(skilled.root / "src/moxi_alpha/held")
    link(skilled, "skills", "gone")
    skilled.git("add", "-A", "src/moxi_alpha")
    assert skilled.findings("7", staged=True) == [
        "src/moxi_alpha/skills:0: is a symlink whose target, gone, isn't committed"]

# --- the status line segment -----------------------------------------------------------------

def test_a_segment_shows_and_triggers_nothing(repo):
    repo.src("module.py", announcement(SEGMENT="'moxi_alpha.segment'"))
    repo.src("segment.py", "from . import clock\ndef segment():\n    return clock.read()\n")
    repo.src("clock.py", "import json\ndef read():\n    return ''\n")
    assert repo.findings() == []
    repo.src("clock.py", """\
        import os
        from os import kill
        def read():
            import subprocess
            os.system("true")
            os.execv("x", [])
            kill(1, 0)
        """)
    found = repo.findings("segment")
    assert [f.split(":")[1] for f in found] == ["4", "5", "6", "7"]


def test_a_segment_may_not_reach_turns(repo):
    repo.src("module.py", announcement(SEGMENT="'moxi_alpha.segment'"))
    repo.src("segment.py", "import urllib.request\nfrom http import client\n")
    assert len(repo.findings("segment")) == 2


# --- staged and whole-tree modes -------------------------------------------------------------

def test_staged_reads_the_index_not_the_working_tree(repo):
    repo.git("init", "-q")
    repo.git("add", ".")
    repo.src("core.py", "from moxi import _thing\n")
    assert repo.findings(staged=True) == []
    assert len(repo.findings("1")) == 1
    repo.git("add", ".")
    repo.src("core.py", "")
    assert len(repo.findings("1", staged=True)) == 1
    assert repo.findings() == []


def test_staged_sees_a_file_deleted_from_the_index(repo):
    repo.src("bad.py", "from moxi import _thing\n")
    repo.git("init", "-q")
    repo.git("add", ".")
    assert len(repo.findings("1", staged=True)) == 1
    repo.git("rm", "-q", "--cached", "src/moxi_alpha/bad.py")
    assert repo.findings(staged=True) == []


def test_staged_reads_a_staged_settings_schema(repo):
    repo.src("module.py", announcement(SETTINGS="'schema.cfg'"))
    repo.src("schema.cfg", "not toml [")
    repo.git("init", "-q")
    repo.git("add", ".")
    assert "is not valid TOML" in repo.findings("7", staged=True)[0]


def test_staged_outside_git_is_an_error(repo):
    with pytest.raises(CheckError, match="not inside a git work tree"):
        check(repo.root, staged=True)


def test_whole_tree_in_git_skips_ignored_files(repo):
    repo.git("init", "-q")
    repo.write(".gitignore", "scratch/\n")
    repo.write("scratch/probe.py", "def (:\n")
    repo.write("notes.py", "def (:\n")
    found = repo.findings("read")
    assert [f.split(":")[0] for f in found] == ["notes.py"]


def test_whole_tree_outside_git_skips_environments(repo):
    repo.write(".venv/lib/x.py", "def (:\n")
    repo.write("build/lib/x.py", "def (:\n")
    assert repo.findings() == []


# --- the command -----------------------------------------------------------------------------

def test_main_exits_0_1_and_2(repo, capsys):
    assert contract.main([str(repo.root)]) == 0
    assert capsys.readouterr().out == ""
    repo.src("core.py", "from moxi import _thing\n")
    assert contract.main([str(repo.root)]) == 1
    out = capsys.readouterr()
    assert out.out.startswith("src/moxi_alpha/core.py:1: check 1: ")
    assert "moxi-dev contract: 1 finding in" in out.err
    assert contract.main(["--staged", str(repo.root)]) == 2
    assert "not inside a git work tree" in capsys.readouterr().err


def test_main_with_staged_judges_the_index(repo, capsys, monkeypatch):
    repo.git("init", "-q")
    repo.git("add", ".")
    repo.src("core.py", "from moxi import _thing\n")
    monkeypatch.chdir(repo.root)
    assert contract.main(["--staged"]) == 0
    assert contract.main([]) == 1
    repo.git("add", ".")
    repo.src("core.py", "")
    capsys.readouterr()
    assert contract.main(["--staged"]) == 1
    assert capsys.readouterr().out.startswith("src/moxi_alpha/core.py:1: check 1: ")


def test_main_names_its_prog(capsys):
    with pytest.raises(SystemExit):
        contract.main(["--help"])
    assert capsys.readouterr().out.startswith(
        "usage: moxi-dev contract [-h] [--staged] [PATH]")


def test_the_module_runs_as_a_script(repo):
    repo.src("core.py", "from moxi import _thing\n")
    proc = subprocess.run([sys.executable, "-m", "moxi_dev.contract", str(repo.root)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 1, proc.stderr
    assert proc.stdout.startswith("src/moxi_alpha/core.py:1: check 1: ")
    assert "python -m moxi_dev.contract: 1 finding in" in proc.stderr


# --- check 8 ---------------------------------------------------------------------------------

WRITES = '''\
import os
import sqlite3
from pathlib import Path


def save(path, data):
    with open(path, "w") as fh:
        fh.write(data)
    os.replace(path, path + ".old")


class Store:
    def keep(self, path):
        Path(path).write_text("x")
        Path(path).write_bytes(b"x")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT)
        with os.fdopen(fd, "a") as fh:
            pass
        with Path(path).open("w") as fh:
            pass
        return sqlite3.connect(path)
'''

READS = '''\
import os
import webbrowser
import zipfile
from pathlib import Path


def read(path, mode):
    with open(path) as fh:
        fh.read()
    with open(path, "rb") as fh:
        fh.read()
    with Path(path).open() as fh:
        fh.read()
    os.open(path, os.O_RDONLY)
    webbrowser.open(path)
    webbrowser.open("https://example.org/")
    zipfile.ZipFile(path).open("data.txt")
    "a.b".replace(".", "/")
'''


def test_every_write_site_is_found_and_reads_are_not(repo):
    repo.src("writes.py", WRITES)
    repo.src("reads.py", READS)
    found = repo.findings("8")
    assert [m.split(": writes a file itself ")[1].split(";")[0] for m in found] == [
        "(save open)", "(save os.replace)", "(Store.keep .write_text)",
        "(Store.keep .write_bytes)", "(Store.keep os.open)", "(Store.keep os.fdopen)",
        "(Store.keep .open)", "(Store.keep sqlite3.connect)"]
    assert found[0].startswith("src/moxi_alpha/writes.py:7:")


def test_an_unreadable_mode_counts_as_a_write(repo):
    repo.src("w.py", "def f(p, m):\n    open(p, m)\n    open(p, mode=m)\n")
    assert len(repo.findings("8")) == 2


def test_listed_sites_pass_and_the_list_counts_each_one(repo):
    repo.src("writes.py", "import os\ndef save(a, b):\n    os.replace(a, b)\n"
                          "    os.replace(b, a)\n")
    repo.write("tests/write-sites.txt", "# a comment\n"
               "src/moxi_alpha/writes.py save os.replace\n")
    found = repo.findings("8")
    assert len(found) == 1 and found[0].startswith("src/moxi_alpha/writes.py:4:")
    repo.write("tests/write-sites.txt", "src/moxi_alpha/writes.py save os.replace\n" * 2)
    assert repo.findings("8") == []


def test_a_line_no_site_matches_is_refused_so_the_list_only_shrinks(repo):
    repo.write("tests/write-sites.txt", "src/moxi_alpha/gone.py save os.replace\n")
    assert repo.findings("8") == [
        "tests/write-sites.txt:0: lists 'src/moxi_alpha/gone.py save os.replace' 1 more "
        "time(s) than src/ has it; remove the line, so the list only shrinks"]


def test_tests_are_not_write_sites(repo):
    repo.write("tests/test_w.py", "def test_w(tmp_path):\n"
                                  "    (tmp_path / 'x').write_text('x')\n")
    assert repo.findings("8") == []


def test_the_core_has_no_write_site_list(tmp_path):
    r = core_repo(tmp_path / "moxi")
    r.src("files.py", "import os\ndef atomic_write(a, b):\n    os.replace(a, b)\n")
    assert r.findings("8") == []


def test_moxi_dev_has_no_write_site_list(tmp_path):
    r = Repo(tmp_path / "moxi-dev", "dev")
    r.pkg = "moxi_dev"
    r.write("pyproject.toml", pyproject("moxi-dev", entry_points={}, scripts={}))
    r.src("__init__.py")
    r.src("new.py", "from pathlib import Path\ndef render(p):\n    Path(p).write_text('x')\n")
    assert r.findings("8") == []
