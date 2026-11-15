"""`moxi publishing ci siblings`: the release test and the published-siblings check.

uv and the venv's interpreter are faked by a runner that records each call and answers
as a test sets; the site-packages check runs for real in its own pytest.
"""

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from conftest import fake_web, git, member_toml, write

from moxi_publishing import release, site_check
from moxi_publishing.flag import Failure

TAG = "proj-a-extra-v0.2.0"
LOCK = """version = 1

[[package]]
name = "pytest"
version = "8.3.1"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "pytest-timeout"
version = "2.3.1"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "pytest-xdist"
version = "3.6.1"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "proj-a"
version = "0.1.0"
source = { editable = "packages/a" }
"""


class Runner:
    """Answers uv and the venv's python as a test sets, recording every call."""

    def __init__(self, installed=None, suite=0, install=0, fence=0):
        self.calls = []
        self.installed = installed or {}
        self.suite = suite
        self.install = install
        self.fence = fence

    def __call__(self, argv, *, cwd=None, env=None, capture=True, timeout_s):
        self.calls.append({"argv": list(argv), "cwd": cwd, "env": env, "capture": capture})
        code, out = 0, b""
        if argv[1:3] == ["pip", "install"]:
            code = self.fence if "--no-deps" in argv else self.install
        elif argv[1:3] == ["-I", "-c"]:
            out = json.dumps(self.installed).encode()
        elif argv[1:3] == ["-m", "pytest"]:
            plugin = Path(env["PYTHONPATH"]) / f"{release.SITE_CHECK}.py"
            self.calls[-1]["plugin"] = plugin.read_text()
            code = self.suite
        return subprocess.CompletedProcess(argv, code, out, b"it broke\n")

    def of(self, *head):
        return [c for c in self.calls if c["argv"][1:1 + len(head)] == list(head)]

    def resolves(self):
        """The installs that resolve the wheel, its siblings and the tools."""
        return [c for c in self.of("pip", "install") if "--no-deps" not in c["argv"]]

    def fences(self):
        """The installs of the test fence alone."""
        return [c for c in self.of("pip", "install") if "--no-deps" in c["argv"]]


def wheel(wheel_dir: Path, dist: str = "proj_a_extra", version: str = "0.2.0") -> Path:
    wheel_dir.mkdir(exist_ok=True)
    path = wheel_dir / f"{dist}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{dist}/__init__.py", "")
        archive.writestr("helper.py", "")
        archive.writestr(f"{dist}-{version}.dist-info/METADATA", "Name: x\n")
    return path


@pytest.fixture
def tagged(released_repo, fake, monkeypatch, tmp_path):
    """The release workspace with a dev group and a lock, `proj-a-extra` tagged, its wheel
    in an artifact directory, and uv found. The workspace holds the test fence's member,
    which the dev group names."""
    repo = released_repo
    root = repo / "pyproject.toml"
    root.write_text(root.read_text().replace('"packages/a-extra"]',
                                             '"packages/a-extra", "packages/moxi-dev"]'))
    write(repo, "packages/moxi-dev/pyproject.toml", member_toml("moxi-dev", "0.1.1"))
    toml = repo / "packages/a-extra/pyproject.toml"
    toml.write_text(toml.read_text() + '[dependency-groups]\ndev = ["pytest", "proj-a", '
                    '"pytest-timeout>=2", "moxi-dev", {include-group = "other"}]\n')
    write(repo, "uv.lock", LOCK)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Test tools")
    git(repo, "tag", "-a", TAG, "-m", "proj-a-extra 0.2.0")
    monkeypatch.setattr("shutil.which", lambda name: f"/fake/bin/{name}")
    monkeypatch.delenv("MOXI_PUBLISHING_PYPI_URL", raising=False)
    wheel(tmp_path / "dist")
    return repo, tmp_path / "dist"


def siblings(repo, artifact, fake, runner, **kw):
    return release.ci_siblings(str(repo), TAG, artifact=str(artifact), web=fake_web(fake),
                               run=runner, **kw)


def test_both_runs_install_only_the_wheel_its_siblings_and_pinned_tools(tagged, fake):
    repo, artifact = tagged
    runner = Runner({"proj-a": ["0.0.9", []]})
    result = siblings(repo, artifact, fake, runner)
    assert result.code == 0, result.lines
    installs = runner.resolves()
    assert [c["argv"][c["argv"].index("--resolution") + 1] for c in installs] == [
        "highest", "lowest-direct"]
    wanted = installs[0]["argv"][installs[0]["argv"].index("highest") + 1:]
    uri = (artifact / "proj_a_extra-0.2.0-py3-none-any.whl").resolve().as_uri()
    # The fence's member is never resolved; the tools it runs beside are, at the lock's
    # versions, after the dev group's own.
    assert wanted == [f"proj-a-extra @ {uri}", "proj-a>=0.0.9", "pytest==8.3.1",
                      "pytest-timeout==2.3.1", "pytest-xdist==3.6.1"]
    assert len(runner.of("venv")) == 2
    assert "extra more: left out, no published proj-a>=0.9" in result.lines
    assert "highest: installed proj-a 0.0.9" in result.lines
    assert result.lines[-1] == "lowest-direct: the suite passed"


def test_the_fence_is_installed_alone_without_its_requirements_after_each_resolution(
        tagged, fake):
    repo, artifact = tagged
    fake.publish("proj-a", "0.1.0")
    runner = Runner({"proj-a": ["0.1.0", []],
                     "other-lib": ["1.4", ["proj-a>=0.1"]]})
    result = siblings(repo, artifact, fake, runner)
    assert result.code == 0, result.lines
    steps = [("venv" if c["argv"][1] == "venv" else "fence" if "--no-deps" in c["argv"]
              else "resolve" if c["argv"][1] == "pip" else c["argv"][2]) for c in runner.calls]
    assert steps == ["venv", "resolve", "-c", "fence", "pytest"] * 2
    for call in runner.fences():
        argv = call["argv"]
        assert argv[argv.index("--no-deps"):] == ["--no-deps", str(repo / "packages/moxi-dev")]
        assert "--resolution" not in argv
    # The floor report reads the venv before the fence is in it.
    assert any(line.startswith("floor raised: proj-a 0.0.9") for line in result.lines)


def test_a_dev_group_without_the_fence_installs_neither_it_nor_its_tools(tagged, fake):
    repo, artifact = tagged
    toml = repo / "packages/a-extra/pyproject.toml"
    toml.write_text(toml.read_text().replace(' "moxi-dev",', ""))
    git(repo, "commit", "-q", "-am", "No fence")
    git(repo, "tag", "-f", "-a", TAG, "-m", "again")
    runner = Runner({"proj-a": ["0.0.9", []]})
    result = siblings(repo, artifact, fake, runner, resolution="highest")
    assert result.code == 0, result.lines
    assert runner.fences() == []
    assert "pytest-xdist==3.6.1" not in runner.resolves()[0]["argv"]


def test_a_failed_fence_install_fails_the_run_before_the_suite(tagged, fake):
    repo, artifact = tagged
    runner = Runner({"proj-a": ["0.0.9", []]}, fence=2)
    result = siblings(repo, artifact, fake, runner, resolution="highest")
    assert result.code == 1
    assert result.lines[-1] == ("highest: the test fence (moxi-dev) can't be installed: "
                                "it broke")
    assert runner.of("-m", "pytest") == []


def test_a_tool_the_fence_runs_beside_missing_from_the_lock_fails(tagged, fake):
    repo, artifact = tagged
    (repo / "uv.lock").write_text(LOCK.split("[[package]]\nname = \"pytest-xdist\"")[0])
    git(repo, "commit", "-q", "-am", "Drop a pin")
    git(repo, "tag", "-f", "-a", TAG, "-m", "again")
    with pytest.raises(Failure) as caught:
        siblings(repo, artifact, fake, Runner())
    assert caught.value.text == ("pytest-xdist, which the test fence (moxi-dev) runs beside, "
                                 "is not in uv.lock")


def test_an_extra_whose_siblings_are_published_is_installed(tagged, fake):
    repo, artifact = tagged
    fake.publish("proj-a", "0.9.1")
    runner = Runner({"proj-a": ["0.9.1", []]})
    result = siblings(repo, artifact, fake, runner, resolution="highest")
    assert result.code == 0, result.lines
    argv = runner.of("pip", "install")[0]["argv"]
    assert any(a.startswith("proj-a-extra[more] @ file:") for a in argv)
    assert "proj-a>=0.9" in argv
    assert "extra more: installed" in result.lines


def test_a_required_sibling_with_no_published_version_refuses_before_any_install(
        tagged, fake):
    repo, artifact = tagged
    fake.index.clear()
    runner = Runner()
    result = siblings(repo, artifact, fake, runner)
    assert result.code == 3
    assert result.lines[-1] == ("no published version admits proj-a>=0.0.9, which "
                                "proj-a-extra requires; release those first")
    assert runner.calls == []


def test_a_floor_the_resolver_raised_is_reported_with_what_raised_it(tagged, fake):
    repo, artifact = tagged
    fake.publish("proj-a", "0.1.0")
    runner = Runner({"proj-a": ["0.1.0", []],
                     "other-lib": ["1.4", ["proj-a>=0.1", "colour; extra == 'x'"]]})
    result = siblings(repo, artifact, fake, runner, resolution="lowest-direct")
    assert result.code == 0
    assert ("floor raised: proj-a 0.0.9 is the lowest published version >=0.0.9 admits; "
            "0.1.0 was installed, raised by other-lib 1.4 (proj-a>=0.1)") in result.lines


def test_the_suite_runs_in_the_package_with_the_site_check_loaded(tagged, fake):
    repo, artifact = tagged
    runner = Runner({"proj-a": ["0.0.9", []]}, suite=1)
    result = siblings(repo, artifact, fake, runner, resolution="highest")
    assert result.code == 1
    assert result.lines[-1] == "highest: the suite failed (exit 1)"
    (call,) = runner.of("-m", "pytest")
    assert call["argv"][3:] == ["-p", release.SITE_CHECK, "-p", "no:cacheprovider"]
    assert Path(call["cwd"]) == repo / "packages/a-extra"
    assert call["capture"] is False
    assert call["env"][site_check.NAMES_ENV] == "helper,proj_a_extra"
    assert call["env"]["PYTHONSAFEPATH"] == "1"
    assert call["plugin"] == Path(site_check.__file__).read_text()


def test_a_failed_install_fails_the_run(tagged, fake):
    repo, artifact = tagged
    result = siblings(repo, artifact, fake, Runner(install=2), resolution="highest")
    assert result.code == 1
    assert result.lines[-1] == "highest: the install failed: it broke"


def test_an_index_from_the_env_is_installed_from(tagged, fake, monkeypatch):
    repo, artifact = tagged
    monkeypatch.setenv("MOXI_PUBLISHING_PYPI_URL", "http://index.example.test/")
    runner = Runner({"proj-a": ["0.0.9", []]})
    siblings(repo, artifact, fake, runner, resolution="highest")
    for call in runner.of("pip", "install"):
        argv = call["argv"]
        assert argv[argv.index("--default-index") + 1] == "http://index.example.test/simple"


def test_no_wheel_of_the_version_is_a_usage_error(tagged, fake, tmp_path):
    repo, _ = tagged
    other = tmp_path / "other"
    wheel(other, version="0.1.9")
    with pytest.raises(Failure) as caught:
        siblings(repo, other, fake, Runner())
    assert caught.value.code == 2
    assert "holds 0 wheel(s) of proj-a-extra 0.2.0" in caught.value.text


def test_a_test_tool_missing_from_the_lock_fails(tagged, fake):
    repo, artifact = tagged
    (repo / "uv.lock").write_text(LOCK.split("[[package]]\nname = \"pytest-timeout\"")[0])
    git(repo, "commit", "-q", "-am", "Drop a pin")
    git(repo, "tag", "-f", "-a", TAG, "-m", "again")
    with pytest.raises(Failure) as caught:
        siblings(repo, artifact, fake, Runner())
    assert "pytest-timeout, in packages/a-extra/pyproject.toml's dev group, is not in " \
           "uv.lock" in caught.value.text


def test_the_cli_wants_the_artifact(capsys):
    from moxi_publishing import cli

    with pytest.raises(SystemExit) as caught:
        cli.main(["ci", "siblings", TAG])
    assert caught.value.code == 2


# -- the site-packages check, for real ------------------------------------------------------

def test_outside_names_a_module_imported_from_elsewhere(tmp_path, monkeypatch):
    (tmp_path / "pkg_here").mkdir()
    (tmp_path / "pkg_here" / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    found = site_check.outside(["pkg_here", "pluggy", "no_such_pkg"], site_check.site_roots())
    assert found[0] == (f"pkg_here imports from {os.path.realpath(tmp_path / 'pkg_here')}"
                        f"/__init__.py, not the installed wheel")
    assert found[1].startswith("no_such_pkg can't be imported")
    assert len(found) == 2


@pytest.mark.parametrize(("names", "code"), [("pkg_here", 1), ("pluggy", 0)])
def test_the_plugin_stops_a_run_that_imports_the_checkout(tmp_path, names, code):
    (tmp_path / "pkg_here").mkdir()
    (tmp_path / "pkg_here" / "__init__.py").write_text("")
    (tmp_path / "conftest.py").write_text("")
    (tmp_path / "test_one.py").write_text("def test_one():\n    pass\n")
    env = {**os.environ, site_check.NAMES_ENV: names}
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "moxi_publishing.site_check",
         "-p", "no:cacheprovider", str(tmp_path)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == code, done.stdout + done.stderr
    if code:
        assert "pkg_here imports from" in done.stdout + done.stderr
        assert "1 passed" not in done.stdout
