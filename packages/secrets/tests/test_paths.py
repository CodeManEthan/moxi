"""Secret paths: `is_secret_path` with `/` and `\\`, in mixed case, never opening a file."""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

import pytest

from moxi_secrets import is_secret_path

SECRET = [
    "auth.json", "home/u/.codex/auth.json",
    ".env", ".env.local", "app/.env.production",
    ".bash_history", "home/u/.zsh_history", "python_history",
    ".ssh/id_ed25519", "home/u/.ssh/config", ".ssh/keys/deploy",
    "server.pem", "tls/ca/root.pem",
    "credentials", ".aws/credentials",
    ".netrc", "home/u/.pgpass",
    "keys/a.p12", "keys/a.pfx", "android/release.jks", "app/debug.keystore",
]

ORDINARY = [
    "README.md", "src/auth.py", "auth.json.example.md", "docs/env.md", "my.env",
    "history.txt", "bash_history.md", "ssh/config", "notes/.sshrc", "pem.txt",
    "credentials.md", "src/credentials/loader.py", "netrc.md", "a.p12.txt",
    "keystore/readme", "",
]


def mixed_case(path: str) -> str:
    return "".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(path))


@pytest.mark.parametrize("path", SECRET)
def test_secret_paths_match_with_either_slash_and_in_any_case(path):
    for form in (path, path.replace("/", "\\"), path.upper(), mixed_case(path),
                 mixed_case(path).replace("/", "\\"), "/abs/" + path, "C:\\x\\" +
                 path.replace("/", "\\")):
        assert is_secret_path(form), form


@pytest.mark.parametrize("path", ORDINARY)
def test_ordinary_paths_do_not(path):
    for form in (path, path.replace("/", "\\"), path.upper(), mixed_case(path)):
        assert not is_secret_path(form), form


def test_pure_paths_are_accepted():
    assert is_secret_path(PurePosixPath("home/u/.ssh/id_rsa"))
    assert is_secret_path(PureWindowsPath(r"C:\Users\u\.ssh\id_rsa"))
    assert is_secret_path(PureWindowsPath(r"C:\Users\u\Secrets\Bundle.P12"))
    assert not is_secret_path(PurePosixPath("src/main.py"))


def test_the_file_is_never_opened(tmp_path):
    # The answer comes from the name alone: a path that does not exist, and a directory
    # that cannot be read, answer the same as a file would.
    assert is_secret_path(tmp_path / "missing" / ".env")
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        assert is_secret_path(locked / "x.pem")
        assert not is_secret_path(locked / "x.txt")
    finally:
        locked.chmod(0o700)


def test_a_path_that_is_not_text_is_refused():
    with pytest.raises(TypeError):
        is_secret_path(b".env")
