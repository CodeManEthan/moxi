"""What a page server and the export command import, measured with `python -X importtime`.

A page server, and the export command, import from ui only `moxi_ui`, `moxi_ui.module`,
`moxi_ui.api`, `moxi_ui.terminal` and `moxi_ui.loopback` with its modules: nothing of the
shell and nothing of the gateway, so starting a page costs no more than the base and the
page's own code. Both run over ui's fixture page, which reaches the base as every page
module does, through `moxi.modules.load("ui", interface=3).api.loopback`.

Beside it, two checks read the source: both package inits hold no import, so the runner's
`-P` refusal runs before anything outside the standard library loads, and no module of
the base imports another part of ui.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from moxi import proc

import moxi_ui
import moxi_ui.loopback
from moxi_ui.loopback.runtime import read_runtime
from moxi_ui.loopback.transport import call, open_connection

MODULE = "fixture"
#: Every `moxi_ui` module a page server or the export command may import.
ALLOWED = {"moxi_ui", "moxi_ui.module", "moxi_ui.api", "moxi_ui.terminal", "moxi_ui.loopback"}
#: Top-level packages neither may import: the shell's process tools, the gateway's passkeys
#: and certificates, and the test tools.
REFUSED = {"psutil", "webauthn", "cryptography", "playwright", "pytest"}

UI = Path(moxi_ui.__file__).parent
BASE = Path(moxi_ui.loopback.__file__).parent


@pytest.fixture
def cwd(tmp_path):
    """A working directory only this user can write, as every spawner passes."""
    path = tmp_path / "cwd"
    path.mkdir(mode=0o700)
    return path


def _environ(site: Path) -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": str(site)}


def _imported(stderr: str) -> list[str]:
    """Every module name in `-X importtime`'s lines."""
    names = []
    for line in stderr.splitlines():
        if not line.startswith("import time:") or "imported package" in line:
            continue
        names.append(line.rsplit("|", 1)[-1].strip())
    return names


def _assert_closure(names: list[str], what: str) -> None:
    assert "moxi_ui.loopback.api" in names, f"{what}: no importtime lines were read"
    ours = [n for n in names if n.split(".")[0] == "moxi_ui"]
    outside = sorted({n for n in ours
                      if n not in ALLOWED and not n.startswith("moxi_ui.loopback.")})
    assert outside == [], f"{what} imports these of ui: {outside}"
    refused = sorted({n for n in names if n.split(".")[0] in REFUSED})
    assert refused == [], f"{what} imports {refused}"


async def _stream_once(info) -> bytes:
    """Open the page's stream, read its head and first event, and close it."""
    reader, writer = await open_connection(info)
    try:
        writer.write(("GET /api/events HTTP/1.1\r\nHost: localhost\r\n"
                      f"X-Moxi-Token: {info.token}\r\n\r\n").encode())
        await writer.drain()
        data = b""
        async with asyncio.timeout(10):
            while b"\r\n\r\n" not in data or b"data:" not in data.partition(b"\r\n\r\n")[2]:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                data += chunk
        return data
    finally:
        writer.close()


def test_a_page_server_imports_only_the_base_of_ui(fixture_site, cwd, tmp_path):
    log = tmp_path / "server.log"
    argv = [sys.executable, "-X", "importtime", "-P", "-m", "moxi_ui.loopback.serve", MODULE]
    with open(log, "wb") as err:
        child = proc.spawn(argv, lifeline="stdin", cwd=cwd, env=_environ(fixture_site),
                           stdout=subprocess.PIPE, stderr=err)
        try:
            ready = child.stdout.readline()
            assert ready.startswith(b"ready "), log.read_text(errors="replace")[-2000:]
            info = read_runtime(MODULE)
            assert info is not None and info.server_id == ready.split()[1].decode()
            assert call(info, "GET", "/api/summary")[0] == 200
            assert call(info, "POST", "/api/add", {"by": 1})[0] == 200
            head = asyncio.run(_stream_once(info))
            assert head.startswith(b"HTTP/1.1 200"), head[:200]
            child.lifeline.close()
            assert child.wait(10) == 0, log.read_text(errors="replace")[-2000:]
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(10)
            child.stdout.close()
    _assert_closure(_imported(log.read_text(errors="replace")), "the page server")


def test_the_export_command_imports_only_the_base_of_ui(fixture_site, cwd):
    argv = [sys.executable, "-X", "importtime", "-P", "-m", "moxi_ui.loopback.export", MODULE]
    done = subprocess.run(argv, cwd=cwd, env=_environ(fixture_site), capture_output=True,
                          text=True, timeout=60)
    assert done.returncode == 0, done.stderr[-2000:]
    assert json.loads(done.stdout)[MODULE]["ok"] is True, done.stdout
    _assert_closure(_imported(done.stderr), "the export command")


def _imports(path: Path) -> list[str]:
    """Every module `path` imports, relative imports resolved against its package."""
    package = ".".join(("moxi_ui", *path.relative_to(UI).parent.parts))
    names = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                base = ".".join(parts[:len(parts) - node.level + 1])
                module = f"{base}.{node.module}" if node.module else base
            else:
                module = node.module or ""
            names.append(module)
            # `from . import x` and `from moxi_ui import x` name modules too.
            names += [f"{module}.{alias.name}" for alias in node.names]
    return names


def test_both_package_inits_hold_no_import():
    # `python -m moxi_ui.loopback.serve` imports both before its `-P` refusal runs.
    for init in (UI / "__init__.py", BASE / "__init__.py"):
        tree = ast.parse(init.read_text(encoding="utf-8"))
        found = [node for node in ast.walk(tree)
                 if isinstance(node, (ast.Import, ast.ImportFrom))]
        assert found == [], f"{init} imports"


def test_the_base_imports_no_other_part_of_ui():
    for path in sorted(BASE.rglob("*.py")):
        for name in _imports(path):
            if name.split(".")[0] != "moxi_ui":
                continue
            assert name == "moxi_ui.loopback" or name.startswith("moxi_ui.loopback."), (
                f"{path.name} imports {name}")
