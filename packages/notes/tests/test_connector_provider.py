"""notes' provider for the connector as the host sees it: the entry named for notes, the
host reached only through `moxi.modules.load`, nothing else in notes importing the
provider, the extra that brings secrets with python-stdnum, and the status rows."""

from __future__ import annotations

import ast
import asyncio
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest
from connector_kit import API, HOST, make_root

if not HOST.ok:
    pytest.skip(f"the connector module is not installed: {HOST.reason}",
                allow_module_level=True)

PACKAGE = Path(__file__).resolve().parents[1]
SRC = PACKAGE / "src" / "moxi_notes"


def _imports(path: Path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            yield [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            yield [("." * node.level) + (node.module or "")]


def test_the_entry_is_named_for_notes_and_loads_the_provider():
    from importlib.metadata import entry_points

    from moxi_notes.connector import PROVIDER

    [entry] = [e for e in entry_points(group=API.GROUP) if e.name == "notes"]
    assert entry.value == "moxi_notes.connector:PROVIDER"
    assert entry.dist.name == "moxi-notes"
    assert entry.load() is PROVIDER
    assert isinstance(PROVIDER, API.Provider)


def test_the_extras_bring_the_host_and_secrets_with_its_personal_data_set():
    extras = tomllib.loads((PACKAGE / "pyproject.toml").read_text())["project"][
        "optional-dependencies"]
    assert any(d.startswith("moxi-connector") for d in extras["connector"])
    assert any(d.startswith("python-stdnum") for d in extras["connector"])
    assert any(d.startswith("moxi-secrets[personal-data]") for d in extras["secrets"])


def test_notes_reaches_the_host_only_through_moxi_modules_load():
    for path in SRC.rglob("*.py"):
        for names in _imports(path):
            assert not any(n.startswith("moxi_connector") for n in names), (path, names)
    for path in (SRC / "connector").glob("*.py"):
        text = path.read_text()
        assert "importlib" not in text and "__import__" not in text, path
    from moxi_notes import connector
    assert connector._interface() is API


def test_nothing_else_in_notes_imports_the_provider():
    for path in SRC.rglob("*.py"):
        if path.parent == SRC / "connector":
            continue
        for names in _imports(path):
            assert not any("connector" in n for n in names), (path, names)


def test_a_missing_host_is_an_import_error_with_the_reason(monkeypatch):
    from moxi_notes import connector
    absent = SimpleNamespace(ok=False, api=None, state="absent",
                             reason="moxi-connector is not installed")
    monkeypatch.setattr(connector.modules, "load", lambda name, interface=None: absent)
    with pytest.raises(ImportError, match="moxi-connector is not installed"):
        connector._interface()


def _started():
    from moxi_notes.connector import build

    notes, provider = build(API)
    asyncio.run(provider.start({}))
    return notes, provider


def _rows(provider):
    return {r.name: (r.level, r.text) for r in provider.status()}


def test_the_status_rows_name_the_root_and_the_chat_sets(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    import moxi_secrets
    _, provider = _started()
    # Only the row reads which engine runs; the redaction itself keeps the real one.
    monkeypatch.setattr(moxi_secrets.engine, "re2_module", lambda: object())
    rows = _rows(provider)
    assert rows["root"] == ("ok", str(root))
    assert rows["redaction"] == ("ok", "the chat sets load, on RE2")
    monkeypatch.setattr(moxi_secrets.engine, "re2_module", lambda: None)
    assert _rows(provider)["redaction"][0] == "warn"
    assert "fallback" in _rows(provider)["redaction"][1]


def test_a_missing_root_fails_its_row_and_the_start_doesnt_raise(tmp_path):
    root = make_root(tmp_path)
    root.rmdir()
    _, provider = _started()
    level, text = _rows(provider)["root"]
    assert level == "fail" and "missing" in text


def test_a_root_that_is_a_file_fails_its_row(tmp_path):
    root = make_root(tmp_path)
    root.rmdir()
    root.write_text("not a folder")
    _, provider = _started()
    level, text = _rows(provider)["root"]
    assert level == "fail" and "isn't a folder" in text


def test_a_chat_set_that_cant_run_fails_the_redaction_row(tmp_path, monkeypatch):
    make_root(tmp_path)
    import moxi_secrets

    def broken(text):
        raise moxi_secrets.SetUnavailable("personal-data needs python-stdnum")

    monkeypatch.setattr(moxi_secrets, "chat_redact", broken)
    _, provider = _started()
    level, text = _rows(provider)["redaction"]
    assert level == "fail" and "python-stdnum" in text


def test_secrets_that_wont_load_fail_the_redaction_row(tmp_path, monkeypatch):
    import sys

    make_root(tmp_path)
    monkeypatch.setitem(sys.modules, "moxi_secrets", None)
    _, provider = _started()
    assert _rows(provider)["redaction"][0] == "fail"


def test_before_its_start_the_redaction_row_says_so():
    from moxi_notes.connector import build

    _, provider = build(API)
    assert _rows(provider)["redaction"][0] == "warn"
