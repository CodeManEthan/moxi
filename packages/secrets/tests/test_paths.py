"""Secret paths: `is_secret_path` with `/` and `\\`, in mixed case, never opening a file;
`secret_path` finding one in a tool call's input."""

from __future__ import annotations

import json
from pathlib import PurePosixPath, PureWindowsPath

import pytest

from moxi_secrets import is_secret_path, secret_path

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


PLAIN = "hunter2-plain"         # a password no redaction set catches


@pytest.mark.parametrize("tool_input", [
    {"file_path": "/work/project/.env"}, {"filePath": "/work/project/.env"},
    {"command": "cat .env | head"}, {"cmd": ["cat", "/work/.env"]},
    json.dumps({"cmd": ["cat", ".env"]})])
def test_a_tool_input_naming_a_secret_path_is_found(tool_input):
    found = secret_path(tool_input)
    assert found is not None and found.endswith(".env")


@pytest.mark.parametrize("command, path", [
    (f"cat > /work/app/.env <<EOF\nDB_PASS={PLAIN}\nEOF", "/work/app/.env"),
    (f"cat ~/.ssh/id_rsa && echo {PLAIN}", "~/.ssh/id_rsa"),
    (f"echo PASS={PLAIN}>/work/app/.env", "/work/app/.env"),
    (f"printf '{{\"path\":\"/work/app/.env\",\"content\":\"DB_PASS={PLAIN}\"}}'",
     "/work/app/.env"),
    (f"printf '{{\"content\":\"DB_PASS={PLAIN}\",\"path\":\"/work/app/.env\"}}'",
     "/work/app/.env"),
    (f"tool --out=/work/app/.env,DB_PASS:{PLAIN}", "/work/app/.env"),
    (f"cat /work/app/.env:{PLAIN}", "/work/app/.env"),
    (f"cat /work/app/.env\x00{PLAIN}", "/work/app/.env"),
    ("*** Begin Patch\n*** Update File: /work/app/.env\n@@\n"
     f"-ADMIN_PASS=old\n+ADMIN_PASS={PLAIN}\n*** End Patch\n", "/work/app/.env")])
def test_a_path_joined_to_other_text_comes_back_alone(command, path):
    assert secret_path({"command": command, "description": "x"}) == path


@pytest.mark.parametrize("value", [
    "cat > /x/.env <<EOF\nA=1\nEOF", "cat ~/.ssh/id_rsa && echo done",
    ["bash", "-lc", "cat /x/.env | head -n 3"], "*** Update File: /x/.env\n+A=1",
    {"a": ["not this", {"b": "then /x/.env with words"}]}])
def test_the_path_found_is_one_word(value):
    found = secret_path(value)
    assert found is not None and found.split() == [found]


@pytest.mark.parametrize("value", [
    {"command": "ls -la src/"}, {"file_path": "/work/README.md"}, "cat docs/env.md",
    ["git", "status"], 42, None, "{not json", json.dumps({"a": "b"})])
def test_an_input_naming_no_secret_path_finds_none(value):
    assert secret_path(value) is None


def test_nesting_past_its_depth_finds_none():
    value: object = "/x/.env"
    for _ in range(40):
        value = [value]
    assert secret_path(value) is None
    shallow: object = "/x/.env"
    for _ in range(30):
        shallow = [shallow]
    assert secret_path(shallow) == "/x/.env"
