"""The clean filter: git's process protocol for real, and its answers one file at a time."""

from __future__ import annotations

import importlib
import io
import sys
from pathlib import Path

import pytest

from moxi_notes import filter as notes_filter

from gate_kit import (allow_line, google_key, notes_repo, private_names, sh, snapshot,
                      token, tree, write)
from notes_kit import configure, git_env

posix = pytest.mark.skipif(sys.platform == "win32",
                           reason="git hooks and the filter's wrapper through POSIX sh; "
                                  "the Windows run is owed")


@pytest.fixture(autouse=True)
def _git(tmp_path, monkeypatch):
    git_env(tmp_path, monkeypatch)


@pytest.fixture
def root() -> Path:
    root = notes_repo("notes/secret-gate")
    configure(action="redact")
    notes_filter.install(root)
    return root


def secret(seed: int = 1) -> str:
    return "intro\nkey = " + token(seed) + "\nmore\n"


def staged_text(root: Path, name: str) -> str:
    return sh(root, "show", f":{name}").stdout


# -- through git --------------------------------------------------------------------------

@posix
def test_install_writes_a_driver_that_is_not_required_and_a_local_attribute(root):
    assert sh(root, "config", "filter.moxi-notes.required").stdout.strip() == "false"
    command = sh(root, "config", "filter.moxi-notes.process").stdout.strip()
    assert command.endswith("' -I -m moxi_notes.filter") and command.startswith("'")
    assert (root / ".git" / "info" / "attributes").read_text() == "* filter=moxi-notes\n"
    notes_filter.install(root)
    assert (root / ".git" / "info" / "attributes").read_text() == "* filter=moxi-notes\n"
    notes_filter.remove(root)
    assert (root / ".git" / "info" / "attributes").read_text() == ""
    assert sh(root, "config", "filter.moxi-notes.process", check=False).returncode == 1
    notes_filter.remove(root)


@posix
def test_one_process_redacts_every_file_of_one_add(root, tmp_path):
    log = tmp_path / "starts.log"
    wrapper = tmp_path / "filter.sh"
    command = notes_filter.process_command()
    wrapper.write_text(f"#!/bin/sh\necho start >> '{log}'\nexec {command}\n")
    wrapper.chmod(0o755)
    sh(root, "config", "filter.moxi-notes.process", str(wrapper))
    write(root, "a.md", secret(1))
    write(root, "b.md", secret(2))
    write(root, "clean.md", "fine\n")
    sh(root, "add", "a.md", "b.md", "clean.md")
    assert log.read_text() == "start\n"
    assert staged_text(root, "a.md") == "intro\nkey = [REDACTED:github-pat]\nmore\n"
    assert staged_text(root, "b.md") == "intro\nkey = [REDACTED:github-pat]\nmore\n"
    assert staged_text(root, "clean.md") == "fine\n"
    assert (root / "a.md").read_text() == secret(1)
    assert sh(root, "status", "--porcelain").stdout == "A  a.md\nA  b.md\nA  clean.md\n"


@posix
def test_a_redacted_blob_passes_prepare_and_both_gates(root, tmp_path):
    private_names("quillon")
    from moxi_repo import api

    api.add(root, "notes/push-gate")
    remote = tmp_path / "remote.git"
    sh(tmp_path, "init", "-q", "--bare", str(remote))
    sh(root, "remote", "add", "origin", str(remote))
    write(root, "a.md", secret())
    write(root, "b.md", "api = " + google_key() + "\n")
    write(root, "c.md", "met Quillon today\nand again\n")
    result = snapshot(root)
    assert result.status == "committed" and result.left == ()
    assert tree(root) >= {"a.md", "b.md", "c.md"}
    assert sh(root, "show", "HEAD:c.md").stdout == "met [REDACTED:private-name] today\nand again\n"
    assert "AIza" not in sh(root, "show", "HEAD:b.md").stdout
    done = sh(root, "push", "-q", "origin", "main", check=False)
    assert done.returncode == 0, done.stderr


@posix
def test_a_file_the_filter_errors_on_is_staged_as_on_disk_and_left_out(root, tmp_path):
    from moxi_repo import api

    api.add(root, "notes/push-gate")
    remote = tmp_path / "remote.git"
    sh(tmp_path, "init", "-q", "--bare", str(remote))
    sh(root, "remote", "add", "origin", str(remote))
    write(root, "keys/alpha.p12", "a key file that matches by its path alone\n")
    write(root, "a.md", secret())
    sh(root, "add", "keys/alpha.p12", "a.md")
    assert staged_text(root, "keys/alpha.p12") == "a key file that matches by its path alone\n"
    assert "[REDACTED:github-pat]" in staged_text(root, "a.md")
    assert (root / "keys/alpha.p12").read_text() == "a key file that matches by its path alone\n"
    sh(root, "reset", "-q")
    result = snapshot(root)
    assert result.status == "committed"
    assert [p for p, _ in result.left] == ["keys/alpha.p12"]
    assert "a.md" in tree(root) and "keys/alpha.p12" not in tree(root)
    sh(root, "add", "keys/alpha.p12")
    api.disable(root, "notes/secret-gate")
    sh(root, "commit", "-q", "-m", "by hand")
    done = sh(root, "push", "-q", "origin", "main", check=False)
    assert done.returncode != 0 and "keys/alpha.p12:1 credentials/pkcs12-file" in done.stderr


def _every_command_works(root: Path) -> None:
    write(root, "plan.md", secret())
    write(root, "clean.md", "fine\n")
    for args in (("add", "plan.md", "clean.md"), ("status",), ("diff", "--cached"),
                 ("stash",), ("stash", "pop")):
        assert sh(root, *args, check=False).returncode == 0, args
    assert staged_text(root, "plan.md") == secret()
    sh(root, "reset", "-q")
    result = snapshot(root)
    assert result.status == "committed"
    assert [p for p, _ in result.left] == ["plan.md"]
    assert "clean.md" in tree(root)


@posix
def test_a_malformed_allowlist_redacts_nothing_and_breaks_nothing(root):
    write(root, ".secrets-allow", allow_line("plan.md", "credentials/github-pat") + "bad\n")
    _every_command_works(root)


@posix
def test_a_filter_that_cant_start_breaks_nothing(root, tmp_path):
    sh(root, "config", "filter.moxi-notes.process", str(tmp_path / "no-such-filter"))
    _every_command_works(root)


# -- one file at a time -------------------------------------------------------------------

def state(**kw) -> notes_filter.State:
    from moxi_secrets import Allowlist

    kw.setdefault("allow", Allowlist())
    kw.setdefault("cap", 10 * 1024 * 1024)
    return notes_filter.State(**kw)


def test_a_hit_in_one_line_is_redacted_in_place():
    status, out = notes_filter.clean(secret().encode(), "a.md", state())
    assert (status, out) == ("success", b"intro\nkey = [REDACTED:github-pat]\nmore\n")


def test_a_one_line_file_that_is_wholly_a_secret_is_left_out():
    assert notes_filter.clean(token().encode(), "a.md", state()) == ("error", None)
    status, out = notes_filter.clean((token() + "\n").encode(), "a.md", state())
    assert (status, out) == ("success", b"[REDACTED:github-pat]\n")


def test_a_path_only_rule_answers_error():
    assert notes_filter.clean(b"anything\n", "keys/alpha.p12", state()) == ("error", None)


def test_a_text_already_one_placeholder_is_answered_as_it_is():
    data = b"[REDACTED:github-pat]"
    assert notes_filter.clean(data, "a.md", state()) == ("success", data)


def test_redacts_last_resort_answers_error_and_a_line_placeholder_stays(monkeypatch):
    import moxi_secrets

    monkeypatch.setattr(moxi_secrets, "redact", lambda *a, **kw: "[REDACTED:github-pat]")
    assert notes_filter.clean(secret().encode(), "a.md", state()) == ("error", None)
    monkeypatch.setattr(moxi_secrets, "redact",
                        lambda *a, **kw: "intro\n[REDACTED:github-pat]\nmore\n")
    assert notes_filter.clean(secret().encode(), "a.md", state())[0] == "success"


def test_an_unscanned_text_answers_error(monkeypatch):
    engine = importlib.import_module("moxi_secrets.engine")
    fallback = importlib.import_module("moxi_secrets.fallback")
    monkeypatch.setattr(engine, "_active", engine.python())
    monkeypatch.setattr(fallback, "BASE_DEADLINE", 0.0)
    monkeypatch.setattr(fallback, "PER_MB", 0.0)
    assert notes_filter.clean(secret().encode(), "a.md", state()) == ("error", None)


def test_binary_and_big_blobs_and_other_actions_pass_unchanged():
    data = b"\0" + secret().encode()
    assert notes_filter.clean(data, "a.md", state()) == ("success", data)
    data = secret().encode()
    assert notes_filter.clean(data, "a.md", state(cap=4)) == ("success", data)
    assert notes_filter.clean(data, "a.md", state(redact=False)) == ("success", data)


def test_an_abort_redacts_nothing():
    assert notes_filter.clean(secret().encode(), "a.md", state(abort="why")) == ("abort", None)


def _pkt(text: str) -> bytes:
    data = text.encode()
    return b"%04x" % (len(data) + 4) + data


def test_the_protocol_answers_each_request():
    content = secret().encode()
    stdin = io.BytesIO(b"".join([
        _pkt("git-filter-client\n"), _pkt("version=2\n"), b"0000",
        _pkt("capability=clean\n"), _pkt("capability=smudge\n"), b"0000",
        _pkt("command=clean\n"), _pkt("pathname=a.md\n"), b"0000", _pkt(content.decode()),
        b"0000",
        _pkt("command=clean\n"), _pkt("pathname=keys/alpha.p12\n"), b"0000",
        _pkt("text\n"), b"0000"]))
    stdout = io.BytesIO()
    assert notes_filter.serve(stdin, stdout, state_of=state) == 0
    out = io.BytesIO(stdout.getvalue())
    assert notes_filter.read_list(out) == ["git-filter-server", "version=2"]
    assert notes_filter.read_list(out) == ["capability=clean"]
    assert notes_filter.read_list(out) == ["status=success"]
    assert notes_filter.read_content(out) == b"intro\nkey = [REDACTED:github-pat]\nmore\n"
    assert notes_filter.read_list(out) == []
    assert notes_filter.read_list(out) == ["status=error"]
    assert out.read() == b""
