# moxi-dev's own tests run inside the kit's fence, as every package's do; pytester
# runs the fence's probes in a pytest of their own.
import json
import shutil
import subprocess
import sys

import pytest

pytest_plugins = ["moxi_dev.testing", "pytester"]

#: The proof that searches the real folders for what a nested session's children wrote.
_SEARCHES = "test_children_of_every_scope_write_nothing_outside_the_run"
_REAL_PLACES = pytest.StashKey[dict]()

#: A Python child's real temp directory and platformdirs' user folders, where installed.
_FLAT = """
import json, tempfile
found = [tempfile.gettempdir()]
try:
    import platformdirs
except ImportError:
    pass
else:
    found += [platformdirs.user_cache_dir(), platformdirs.user_data_dir(),
              platformdirs.user_config_dir(), platformdirs.user_state_dir()]
print(json.dumps(found))
"""


def pytest_collection_finish(session: pytest.Session) -> None:
    """Before the test loop, so before any fence: where uv and Python write under the
    environment pytest started with, for the proof that searches them. Asked only when
    that proof runs."""
    if not any(getattr(item, "originalname", item.name) == _SEARCHES
               for item in session.items):
        return
    from moxi_dev import testing

    env, uv = testing.started_env(), shutil.which("uv")
    deep = []
    if uv is not None:
        for argv in (["cache", "dir"], ["python", "dir"], ["python", "dir", "--bin"],
                     ["tool", "dir"], ["tool", "dir", "--bin"]):
            done = subprocess.run([uv, *argv], env=env, capture_output=True, text=True,
                                  timeout=60, check=True)
            deep.append(done.stdout.strip())
    done = subprocess.run([sys.executable, "-c", _FLAT], env=env, capture_output=True,
                          text=True, timeout=60, check=True)
    session.config.stash[_REAL_PLACES] = {"deep": deep, "flat": json.loads(done.stdout)}


@pytest.fixture
def real_places(request: pytest.FixtureRequest) -> dict:
    """uv's real folders (`deep`, searched whole) and the real temp directory and
    platformdirs' user folders (`flat`, searched one level), found before any fence."""
    return request.config.stash[_REAL_PLACES]
