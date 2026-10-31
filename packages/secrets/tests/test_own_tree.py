"""The package's own files raise no hit: scanned in-process with their repo paths and the
credentials and broad sets, and by the gitleaks binary of the pinned tag, run as the bump
runs it.

The binary's command is the bump's: working directory `src/moxi_secrets/rules/`, the
package directory as an absolute path for the source, `--config credentials.toml`, the
default decode depth. Flags that only shape the report (`--no-banner`, `--redact`, a JSON
report in the test's tmp) are added. Both scans see the directory as it is, compiled
files in `__pycache__` included, since the binary does.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from moxi_secrets import scan

scan_mod = importlib.import_module("moxi_secrets.scan")

PACKAGE = Path(__file__).resolve().parents[1]
RULES = PACKAGE / "src" / "moxi_secrets" / "rules"
#: The package's place in the repo, which every repo caller scans with.
REPO_PATH = "packages/secrets"
MODULE = "github.com/zricethezav/gitleaks/v8"

#: Go's module cache as this process started, before the test fence moved the home.
_GO_MODCACHE = os.environ.get("GOMODCACHE") or os.path.join(
    os.environ.get("GOPATH") or os.path.join(os.path.expanduser("~"), "go"), "pkg", "mod")


def pinned_tag() -> str:
    return scan_mod.load_sets().upstream["tag"]


def tree():
    for path in sorted(PACKAGE.rglob("*")):
        if path.is_file():
            yield path, f"{REPO_PATH}/{path.relative_to(PACKAGE).as_posix()}"


def test_the_package_tree_raises_no_hit_in_process():
    found, files = [], 0
    for path, repo_path in tree():
        files += 1
        text = path.read_bytes().decode("utf-8", "surrogateescape")
        for hit in scan(text, ("credentials", "broad"), path=repo_path):
            found.append((repo_path, hit.rule, hit.line))
    assert files > 20
    assert found == []


def test_sets_json_is_kept_by_its_path_allowance_alone():
    text = (RULES / "sets.json").read_text(encoding="utf-8")
    assert scan(text, ("credentials",), path=f"{REPO_PATH}/src/moxi_secrets/rules/x.json")
    assert scan(text, ("credentials",),
                path=f"{REPO_PATH}/src/moxi_secrets/rules/sets.json") == []


# -- the binary -----------------------------------------------------------------------------

def run_gitleaks(prefix, tmp_path, env=None):
    report = tmp_path / "gitleaks.json"
    command = [*prefix, "dir", str(PACKAGE), "--config", "credentials.toml",
               "--no-banner", "--redact", "--report-format", "json",
               "--report-path", str(report)]
    done = subprocess.run(command, cwd=RULES, env=env, capture_output=True, text=True,
                          timeout=600)
    if not report.exists():
        pytest.fail(f"gitleaks wrote no report (exit {done.returncode}):\n"
                    f"{done.stderr[-2000:]}")
    findings = json.loads(report.read_text(encoding="utf-8") or "[]")
    assert [(f["RuleID"], os.path.relpath(f["File"], PACKAGE), f["StartLine"])
            for f in findings] == []
    assert done.returncode == 0


def binary_tag(binary: str) -> str | None:
    """The gitleaks tag a binary was built from: its own `version`, else its Go build
    information."""
    out = subprocess.run([binary, "version"], capture_output=True, text=True,
                         timeout=30).stdout.strip()
    if re.fullmatch(r"v?[0-9]+\.[0-9]+\.[0-9]+", out):
        return "v" + out.lstrip("v")
    go = shutil.which("go")
    if go is None:
        return None
    info = subprocess.run([go, "version", "-m", binary], capture_output=True, text=True,
                          timeout=30).stdout
    m = re.search(r"^\s*mod\s+" + re.escape(MODULE) + r"\s+(v\S+)", info, re.M)
    return m.group(1) if m else None


def test_the_pinned_gitleaks_binary_raises_no_hit(tmp_path):
    binary = shutil.which("gitleaks")
    if binary is None:
        pytest.skip("no gitleaks binary on PATH; the slow tier runs the pinned tag with "
                    "go run")
    tag, pin = binary_tag(binary), pinned_tag()
    if tag != pin:
        pytest.skip(f"the gitleaks on PATH is {tag or 'of an unknown tag'}, not the pinned "
                    f"{pin}; the slow tier runs the pinned tag with go run")
    run_gitleaks([binary], tmp_path)


@pytest.mark.slow
def test_go_run_of_the_pinned_tag_raises_no_hit(tmp_path, fence):
    go = shutil.which("go")
    if go is None:
        pytest.skip("Go is not on PATH")
    pin = pinned_tag()
    download = Path(_GO_MODCACHE) / "cache" / "download"
    if not (download / MODULE / "@v" / f"{pin}.zip").exists():
        pytest.skip(f"gitleaks {pin} is not in Go's module cache; the bump, which has the "
                    "network, runs it")
    # Offline: the module cache's download directory serves as the proxy and is only
    # read. The build, its cache and the modules it unpacks stay in the test's tmp.
    env = dict(fence.env, GOPROXY=download.as_uri(), GOSUMDB="off", GOTOOLCHAIN="local",
               GOFLAGS="-modcacherw", GOMODCACHE=str(tmp_path / "gomod"),
               GOCACHE=str(tmp_path / "gocache"), GOPATH=str(tmp_path / "gopath"),
               GOTMPDIR=str(tmp_path))
    run_gitleaks([go, "run", f"{MODULE}@{pin}"], tmp_path, env)
