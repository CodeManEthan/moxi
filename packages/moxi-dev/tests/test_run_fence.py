"""The run's fence: every process the tests start, from a fixture of any scope, writes
only inside the run's roots.

Three proofs. Where a child would write, as uv and Python answer under each kind of
environment a test hands a child. What children wrote, from a nested pytest session's
fixtures of every scope, in a sentinel tree and, by a token, in the real folders. And a
pytest run inside a test, which borrows the outer run and leaves it as it was.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path

import pytest

from moxi_dev import testing

UV = shutil.which("uv")
needs_uv = pytest.mark.skipif(UV is None, reason="uv is not on PATH")

#: What uv answers for each folder it writes: the command's arguments.
UV_DIRS = {"UV_CACHE_DIR": ["cache", "dir"], "UV_PYTHON_INSTALL_DIR": ["python", "dir"],
           "UV_PYTHON_BIN_DIR": ["python", "dir", "--bin"], "UV_TOOL_DIR": ["tool", "dir"],
           "UV_TOOL_BIN_DIR": ["tool", "dir", "--bin"]}

#: A Python child's answer: where it puts byte code, its temp directory and home, and
#: the user folders platformdirs (where installed) and the core give a module.
WHERE = """
import json, os, sys, tempfile
out = {"no_byte_code": bool(sys.flags.dont_write_bytecode),
       "pycache_prefix": sys.pycache_prefix, "base_prefix": sys.base_prefix,
       "folders": [tempfile.gettempdir(), os.path.expanduser("~")]}
try:
    import platformdirs
except ImportError:
    pass
else:
    out["folders"] += [platformdirs.user_cache_dir(), platformdirs.user_data_dir(),
                       platformdirs.user_config_dir(), platformdirs.user_state_dir()]
if not sys.flags.isolated:
    from moxi import paths
    layout = paths.Layout("probe")
    out["folders"] += [str(layout.data_dir()), str(layout.state_dir()),
                       str(layout.cache_dir()), str(layout.config_dir())]
print(json.dumps(out))
"""


def _system_env() -> dict[str, str]:
    """An environment built from nothing: on Windows, the system's own variables a
    process needs to start at all (`SYSTEMROOT` above all), and nothing else."""
    if not testing.WINDOWS:
        return {}
    return {key: os.environ[key] for key in testing.WINDOWS_KEPT_ENV if key in os.environ}


@pytest.fixture(scope="session")
def run_env() -> dict[str, str]:
    """The run's environment, as a fixture of the widest scope sees it."""
    return dict(os.environ)


def _ask(argv: list[str], env: dict[str, str], cwd: Path) -> str:
    done = subprocess.run(argv, env=env, cwd=cwd, capture_output=True, text=True,
                          timeout=testing.PROC_WAIT_S * 3, stdin=subprocess.DEVNULL)
    assert done.returncode == 0, (argv, done.stderr)
    return done.stdout.strip()


ENVS = ["run", "test", "uv_env", "empty", "stripped", "a_home"]


@needs_uv
@pytest.mark.parametrize("kind", ENVS)
def test_every_child_would_write_inside_the_run(kind, run_env, uv_env, tmp_path, request):
    own_home = tmp_path / "own-home"
    env = {"run": run_env, "test": dict(os.environ), "uv_env": uv_env,
           "empty": _system_env(),
           # The shape offline_uv_env builds: no uv or Python variable of the kit's.
           "stripped": {key: value for key, value in os.environ.items()
                        if not key.startswith(("UV_", "PYTHON"))},
           "a_home": {**_system_env(), "HOME": str(own_home)}}[kind]
    cwd = tmp_path / "cwd"              # no project, no .venv, for uv to find
    cwd.mkdir()
    answers = {key: _ask([UV, *argv], env, cwd) for key, argv in UV_DIRS.items()}
    for key, folder in answers.items():
        assert testing._in_roots(folder), f"{key}: {folder}"
    # The run's one uv cache, under xdist the one the controller named for every worker,
    # outside the worker's own temp root.
    cache = Path(answers["UV_CACHE_DIR"])
    assert cache == Path(testing._RUN.child["UV_CACHE_DIR"])
    workerinput = getattr(request.config, "workerinput", None)
    if workerinput is not None:
        assert str(cache) == workerinput[testing._UV_CACHE_KEY]
        own = request.config._tmp_path_factory.getbasetemp()
        assert not cache.is_relative_to(own) and cache.parent == own.parent
    # The Python uv runs is the suite's.
    ran = json.loads(_ask([UV, "run", "--no-project", "--offline", "python", "-c", WHERE],
                          env, cwd))
    assert ran["base_prefix"] == sys.base_prefix
    for argv in ([sys.executable, "-c", WHERE], [sys.executable, "-I", "-c", WHERE]):
        said = json.loads(_ask(argv, env, cwd))
        assert said["no_byte_code"] or (said["pycache_prefix"]
                                        and testing._in_roots(said["pycache_prefix"])), \
            (argv[1], said)
        for folder in said["folders"]:
            assert testing._in_roots(folder), (argv[1], folder)
    if kind == "a_home":
        assert json.loads(_ask([sys.executable, "-c", WHERE], env, cwd))["folders"][1] \
            == str(own_home)


# --- what children wrote ------------------------------------------------------------------

#: What the nested session's children write: a file named with the token in the home,
#: the temp directory and, where it is installed, platformdirs' cache folder.
WRITE = """
import os, sys, tempfile
tag = sys.argv[1]
places = [os.path.expanduser("~"), tempfile.gettempdir()]
try:
    import platformdirs
except ImportError:
    pass
else:
    places.append(platformdirs.user_cache_dir())
for place in places:
    os.makedirs(place, exist_ok=True)
    open(os.path.join(place, tag), "w").close()
"""

#: The nested session: a session-scoped fixture, a module-scoped one and a test, each
#: starting the same children and uv commands.
NESTED = '''
import json, os, subprocess, sys
from pathlib import Path

import pytest

from moxi_dev import testing

TOKEN, NAME, WHEEL, UV, WRITE = {values}


def system_env():
    if not testing.WINDOWS:
        return {{}}
    return {{k: os.environ[k] for k in testing.WINDOWS_KEPT_ENV if k in os.environ}}


def uv(*args, cwd, check=True):
    done = subprocess.run([UV, *args], cwd=cwd, capture_output=True, text=True,
                          timeout=300, stdin=subprocess.DEVNULL)
    assert done.returncode == 0 or not check, (args, done.stderr)


def children(where, work):
    tag = TOKEN + "-" + where
    for env in (None, system_env()):
        subprocess.run([sys.executable, "-c", WRITE, tag], env=env, check=True, timeout=60)
    work.mkdir(parents=True, exist_ok=True)
    venv = work / "venv"
    uv("venv", str(venv), cwd=work)
    uv("pip", "install", "--offline", "--python", str(venv), WHEEL, cwd=work)
    uv("pip", "uninstall", "--python", str(venv), NAME, cwd=work)
    uv("tool", "install", "--offline", WHEEL, cwd=work)
    project = work / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        f'[project]\\nname = "user-{{where}}"\\nversion = "0.1"\\n'
        f'requires-python = ">=3.12"\\ndependencies = ["{{NAME}}"]\\n'
        f'[tool.uv.sources]\\n{{NAME}} = {{{{ path = {{json.dumps(WHEEL)}} }}}}\\n')
    uv("lock", "--offline", cwd=project)
    uv("sync", "--offline", cwd=project)
    uv("run", "--offline", "python", "-c", f"import {{NAME}}", cwd=project)
    built = work / "built"
    (built / "src" / f"built_{{where}}").mkdir(parents=True)
    (built / "src" / f"built_{{where}}" / "__init__.py").write_text("")
    (built / "pyproject.toml").write_text(
        f'[project]\\nname = "built-{{where}}"\\nversion = "0.1"\\n'
        f'requires-python = ">=3.12"\\n[build-system]\\nrequires = ["uv_build>=0.8,<2"]\\n'
        f'build-backend = "uv_build"\\n')
    uv("build", "--offline", "--wheel", "-o", str(work / "dist"), cwd=built)
    uv("python", "find", "--system", cwd=work, check=False)


@pytest.fixture(scope="session")
def session_wide(tmp_path_factory):
    children("session", tmp_path_factory.mktemp("session"))


@pytest.fixture(scope="module")
def module_wide(tmp_path_factory, session_wide):
    children("module", tmp_path_factory.mktemp("module"))


def test_children(module_wide, tmp_path):
    children("test", tmp_path / "work")
    print("REPORT", json.dumps({{"short": testing._RUN.short}}))
'''


def _wheel(where: Path, name: str) -> Path:
    """A wheel of package `name` with one console script, `name`."""
    where.mkdir(parents=True)
    path = where / f"{name}-0.1-py3-none-any.whl"
    info = f"{name}-0.1.dist-info"
    files = {f"{name}/__init__.py": "def main():\n    return 0\n",
             f"{info}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: 0.1\n",
             f"{info}/WHEEL": "Wheel-Version: 1.0\nGenerator: hand\nRoot-Is-Purelib: true\n"
                              "Tag: py3-none-any\n",
             f"{info}/entry_points.txt": f"[console_scripts]\n{name} = {name}:main\n"}
    record = "".join(f"{file},,\n" for file in files) + f"{info}/RECORD,,\n"
    with zipfile.ZipFile(path, "w") as wheel:
        for file, text in files.items():
            wheel.writestr(file, text)
        wheel.writestr(f"{info}/RECORD", record)
    return path


def _tree(root: Path) -> dict[str, tuple[int, int]]:
    """Every path under `root` with its size and modification time."""
    found = {}
    for parent, dirs, files in os.walk(root):
        for name in (*dirs, *files):
            path = os.path.join(parent, name)
            stat = os.lstat(path)
            found[os.path.relpath(path, root)] = (stat.st_size, stat.st_mtime_ns)
    return found


def _named(root: str, token: str, *, deep: bool) -> list[str]:
    """The paths in `root` (and, `deep`, under it) whose name holds `token`."""
    if not os.path.isdir(root):
        return []
    if not deep:
        return [os.path.join(root, name) for name in os.listdir(root) if token in name]
    return [os.path.join(parent, name) for parent, dirs, files in os.walk(root)
            for name in (*dirs, *files) if token in name]


@needs_uv
@pytest.mark.build
@pytest.mark.touches("packages/moxi-dev/src/moxi_dev/testing.py")
def test_children_of_every_scope_write_nothing_outside_the_run(pytester, tmp_path,
                                                               monkeypatch, real_places):
    token = uuid.uuid4().hex[:12]
    name = f"mxprobe{token}"
    wheel = _wheel(tmp_path / "wheels", name)
    # The nested run's started environment: every home, temp directory and uv folder
    # a sentinel directory here.
    sentinel = tmp_path / "sentinel"
    keys = [*testing.HARNESS_HOMES, "TMPDIR", *testing.UV_DIRS_ENV]
    if testing.WINDOWS:
        keys += [*testing.WINDOWS_HOMES, "TEMP", "TMP"]
    for key in keys:
        (sentinel / key.lower()).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(key, str(sentinel / key.lower()))
    before = _tree(sentinel)
    pytester.makeconftest('pytest_plugins = ["moxi_dev.testing"]\n')
    values = (token, name, str(wheel), UV, WRITE)
    pytester.makepyfile(test_nested=NESTED.format(values=repr(values)))
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-p", "no:randomly",
                                           "-s", timeout=900)
    result.assert_outcomes(passed=1)
    assert _tree(sentinel) == before
    (line,) = [line for line in result.outlines if "REPORT " in line]
    nested = json.loads(line.split("REPORT ", 1)[1])
    assert nested["short"] and not os.path.exists(nested["short"])
    found = [path for folder in real_places["deep"] for path in _named(folder, token,
                                                                         deep=True)]
    found += [path for folder in real_places["flat"] for path in _named(folder, token,
                                                                          deep=False)]
    assert found == []


# --- a loop inside a loop -----------------------------------------------------------------

INNER = '''
import json, os, subprocess, sys
from moxi_dev import testing

def test_inner(tmp_path):
    cache = subprocess.run([sys.executable, "-c",
                            "import os; print(os.environ['UV_CACHE_DIR'])"],
                           capture_output=True, text=True, check=True, timeout=60)
    print("INNER", json.dumps({"started": testing.started_env(),
                               "cache": cache.stdout.strip(), "short": testing._RUN.short}))
'''


def test_a_loop_inside_a_loop_leaves_the_run_as_it_was(pytester):
    env, short, child = dict(os.environ), testing._RUN.short, dict(testing._RUN.child)
    started = testing.started_env()
    pytester.makeconftest('pytest_plugins = ["moxi_dev.testing"]\n')
    pytester.makepyfile(test_inner=INNER)
    result = pytester.runpytest("-p", "no:cacheprovider", "-p", "no:randomly", "-s")
    result.assert_outcomes(passed=1)
    (line,) = [line for line in result.outlines if "INNER " in line]
    inner = json.loads(line.split("INNER ", 1)[1])
    assert inner["started"] == env                  # the outer test's, as it started it
    assert inner["short"] == short                  # no second run directory
    assert inner["cache"] == child["UV_CACHE_DIR"]  # no second uv cache
    assert dict(os.environ) == env
    assert (testing._RUN.short, testing._RUN.child) == (short, child)
    assert testing.started_env() == started
    assert testing._RUN.depth == 1
    assert os.path.isdir(short) and os.path.isdir(child["UV_CACHE_DIR"])
    assert subprocess.Popen.__init__ is testing._seam_init
