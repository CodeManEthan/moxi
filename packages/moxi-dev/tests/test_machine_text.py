"""The machine-text check: the home folders, private names and package-manager commands it
refuses, the files it reads, and the mark that lets a genuine use stand.

A command this file spells as data is written with a `|` that `cmd` removes, so the
file passes the check it tests.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from moxi_dev import machine_text
from moxi_dev.machine_text import (CODE, Hit, SetUnavailable, check_text, home_paths, is_test,
                                   manager_commands, read, scan)


def cmd(text: str) -> str:
    """`text` with each `|` removed: a command this file holds as data."""
    return text.replace("|", "")


#: A made-up private-names list: one word and one entry matched anywhere.
NAMES = ("ann", "~/vault")


def made_up_lines(text: str, path: str) -> set[int]:
    """The lines of `text` holding a `NAMES` entry, no letter or digit at either end."""
    found = set()
    for entry in NAMES:
        pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(entry) + r"(?![A-Za-z0-9])",
                             re.I)
        for match in pattern.finditer(text):
            found.add(text.count("\n", 0, match.start()) + 1)
    return found


@pytest.fixture
def names(monkeypatch):
    """The private-name rule reads `NAMES`, never the contributor's own list."""
    monkeypatch.setattr(machine_text, "private_lines", made_up_lines)


@pytest.fixture
def no_names(monkeypatch):
    monkeypatch.setattr(machine_text, "private_lines", lambda text, path: set())


# --- the home-folder rule ----------------------------------------------------------------

@pytest.mark.parametrize("line, home", [
    ("/home/ann/src/app", "/home/ann"),
    ("/Users/ana/Library", "/Users/ana"),
    ("C:\\Users\\Jane\\AppData", "C:\\Users\\Jane"),
    (r'"C:\\Users\\ann"', r"C:\\Users\\ann"),
    ("C:/Users/ann/x", "C:/Users/ann"),
    ("d:\\users\\bo", "d:\\users\\bo"),
    ("/var/home/bob/.config", "/var/home/bob"),
    ("file:///home/ann/x", "/home/ann"),
    ("cd /home/u", "/home/u"),
    ("(/Users/Publicity)", "/Users/Publicity"),
    ("/home/someone_2/x", "/home/someone_2"),
])
def test_a_home_path_hits(line, home):
    assert home_paths(line) == [home]


@pytest.mark.parametrize("line", [
    "/home/you/src/app", "/home/me2/x", "/home/USER", "/home/Name/x", "/home/username",
    "/Users/<name>/Library", "/Users/Shared/x", "/Users/Default", "C:\\Users\\Public",
    "C:\\Users\\All Users\\x", "%USERPROFILE%\\x", "C:\\Users\\%USERNAME%", "/home/$USER/x",
    "/home/${USER}/x", "/home/{user}/x", "/home/*/x", "/home/<you>", "docs/home/moxi",
    "/x/home/u", "~/home/u", "a.home/u", "/homebrew", "/home/", "~/.config/moxi",
    "~/code/app", "/root/.cache", "/opt/homebrew/bin", "XC:\\Users\\ann",
])
def test_a_near_miss_is_no_home_path(line):
    assert home_paths(line) == []


def test_each_home_path_on_a_line_is_found():
    assert home_paths("/home/ann and /Users/bo") == ["/home/ann", "/Users/bo"]


# --- the package-manager rule -------------------------------------------------------------

@pytest.mark.parametrize("line, command", [
    (cmd("sudo apt-get| install -y tmux"), cmd("apt-get| install")),
    (cmd("dnf| install tmux"), cmd("dnf| install")),
    (cmd("brew| install tmux"), cmd("brew| install")),
    (cmd("pacman| -S tmux"), cmd("pacman| -S")),
    (cmd("pacman| -Syu"), cmd("pacman| -Syu")),
    (cmd("winget| install Git.Git"), cmd("winget| install")),
    (cmd("apt| --yes update"), cmd("apt| update")),
    (cmd("run `zypper| remove x`"), cmd("zypper| remove")),
    (cmd('["apt-get"|, "install", "-y", "x"]'), cmd("apt-get| install")),
    (cmd("('dnf'|, '-y', 'install')"), cmd("dnf| install")),
])
def test_a_package_manager_command_hits(line, command):
    assert manager_commands(line) == [command]


@pytest.mark.parametrize("line", [
    "pip install x", "uv tool install moxi", "npm install", "an apt name",
    "install it with apt-get", 'fence.short / "brew" / "bin"', "/opt/homebrew/bin",
    'values["port"]', 'Socket("port", 8460)', "port 8460", "the support port",
    "Brew install", "xbrew install", "./brew install", "brew installer",
    "systemctl --user restart moxi", "launchctl load x", "pacman -s x",
])
def test_a_near_miss_is_no_command(line):
    assert manager_commands(line) == []


# --- the private-name rule ----------------------------------------------------------------

def test_a_private_name_hits_any_read_file_and_prints_no_name(names):
    text = "ask ann\nannual\nsee ~/vault/rounds\n"
    for path in ("packages/a/README.md", "packages/a/tests/test_x.py", "README.md"):
        assert check_text(path, text) == [Hit(path, 1, "name"), Hit(path, 3, "name")]
    assert "ann" not in str(Hit("a.md", 1, "name"))


def test_an_empty_list_finds_nothing(no_names):
    assert check_text("a.md", "ask ann\n") == []


def test_the_list_is_the_contributor_s_own_secrets_config():
    """With no seam, the rule reads secrets' config: here the fence's, made up."""
    from moxi.paths import Layout

    config = Layout("secrets").config_file()
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text('[private_names]\nwords = ["ann"]\nanywhere = ["~/vault"]\n',
                      encoding="utf-8")
    text = "ask ann\nannual\nann_x\nsee ~/vault/rounds\n"
    assert [hit.line for hit in check_text("a.md", text)] == [1, 3, 4]
    config.write_text("[private_names\n", encoding="utf-8")
    with pytest.raises(SetUnavailable):
        check_text("a.md", text)


def test_a_list_secrets_can_t_read_stops_the_scan(repo, monkeypatch):
    def unreadable(text, path):
        raise SetUnavailable("private_names.words: not a list")

    write(repo, "README.md", "hello\n")
    monkeypatch.setattr(machine_text, "private_lines", unreadable)
    with pytest.raises(SetUnavailable):
        scan(repo)
    assert machine_text.unreadable(SetUnavailable("bad")) == (
        "the machine-text step could not read your private-names list (bad); "
        "run moxi config secrets check")


# --- shipped text and tests ---------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "packages/a/tests/test_x.py",
    "packages/a/tests/data/x.json",
    "packages/a/web/test/page.test.ts",
    "packages/a/web/test/helpers.ts",
    "packages/a/src/moxi_a/claude/p/tests/run.mjs",
    "packages/a/conftest.py",
    "packages/a/test_x.py",
    "packages/a/web/src/page.test.ts",
])
def test_a_test_is_held_to_the_names_and_managers_only(names, path):
    text = "/home/bob/x\n" + cmd("brew| install x\n") + "ask ann\n"
    assert check_text(path, text) == [Hit(path, 2, "manager", cmd("brew| install")),
                                      Hit(path, 3, "name")]
    assert is_test(path)


@pytest.mark.parametrize("path", [
    "packages/a/src/moxi_a/x.py", "packages/a/README.md", "packages/a/CHANGELOG.md",
    "packages/a/web/src/testing.ts", "packages/a/contest.py", "README.md",
    "tests/x.py", "tools/web/build.mjs", "packages/tests",
])
def test_anything_else_is_shipped_text(path):
    assert not is_test(path)
    assert check_text(path, "/home/bob/x\n") == [Hit(path, 1, "home", "/home/bob")]


@pytest.mark.parametrize("path", [
    ".github/workflows/ci.yml", "packages/moxi/src/moxi/_vendor/lib/x.py",
    "packages/a/src/moxi_a/web/page.js", "uv.lock", "package-lock.json",
    "packages/a/web/package-lock.json", "packages/moxi-dev/renames.tsv", ".residue-allow",
    ".refs-allow", ".secrets-allow", ".public-paths", "LICENSE", "packages/a/LICENSE",
    "packages/a/src/moxi_a/data/LICENSE-lib",
])
def test_the_files_the_check_leaves_alone(path):
    assert not read(path)


@pytest.mark.parametrize("path", [
    "packages/a/CHANGELOG.md", "packages/a/web/src/page.ts", "packages/a/web/build.json",
    "packages/a/src/moxi_a/page.py", "packages/a/src/moxi_a/web.py", "packages/moxi/src/x.py",
    "packages/a/pyproject.toml", "packages/a/src/moxi_a/settings.toml", "README.md",
    "CONTRIBUTING.md", "tools/web/build.mjs", "packages/a/docs/x.md",
    "packages/a/scripts/x.sh",
])
def test_the_files_the_check_reads(path):
    assert read(path)


# --- the mark -----------------------------------------------------------------------------

def test_a_marked_line_lets_its_home_path_and_command_stand(no_names):
    source = (f"a = '/home/bob'  # noqa: {CODE} -- one user's sample\n"
              + cmd(f"b = 'brew| install x'  # noqa: {CODE} -- macOS\n")
              + "c = '/home/bob'\n")
    assert check_text("a.py", source) == [Hit("a.py", 3, "home", "/home/bob")]


def test_the_mark_takes_the_file_s_own_comment_syntax(no_names):
    doc = cmd(f"Run `brew| install x`. <!-- noqa: {CODE} -- written for macOS -->\n")
    web = f"const p = '/Users/bo';  // noqa: {CODE} -- the macOS sample\n"
    shell = cmd(f"dnf| install x  # noqa: {CODE} -- Fedora\n")
    assert check_text("a.md", doc) == []
    assert check_text("a.ts", web) == []
    assert check_text("a.sh", shell) == []


def test_the_mark_stands_on_a_docstring_s_inner_line_in_the_text_itself(no_names):
    source = f'"""One.\n\nThe runner is C:\\Users\\bot (noqa: {CODE} -- the runner)\n"""\n'
    assert check_text("a.py", source) == []


def test_the_mark_may_name_other_codes_beside_its_own(no_names):
    source = f"x = '/home/bob'  # noqa: MX5, {CODE} -- a sample\n"
    assert check_text("a.py", source) == []


def test_a_mark_does_not_let_a_private_name_stand(names):
    source = (f"a = '/home/bob ann'  # noqa: {CODE} -- a sample\n"
              f"b = 'ann'  # noqa: {CODE} -- a sample\n")
    assert check_text("a.py", source) == [
        Hit("a.py", 1, "name"), Hit("a.py", 2, "name"), Hit("a.py", 2, "unused")]


def test_a_mark_with_no_reason_is_refused_and_lets_nothing_stand(no_names):
    for mark in (f"# noqa: {CODE}", f"# noqa: {CODE} --", f"# noqa: {CODE} --  "):
        source = f"x = '/home/bob'  {mark}\n"
        assert check_text("a.py", source) == [
            Hit("a.py", 1, "home", "/home/bob"), Hit("a.py", 1, "reason")], mark


def test_a_mark_on_another_line_or_for_another_code_lets_nothing_stand(no_names):
    source = f"# noqa: {CODE} -- a sample\nx = '/home/bob'  # noqa: MX5 -- a quotation\n"
    assert check_text("a.py", source) == [
        Hit("a.py", 1, "unused"), Hit("a.py", 2, "home", "/home/bob")]


def test_a_mark_on_a_line_with_nothing_refused_is_unused(no_names):
    assert check_text("a.py", f"x = 1  # noqa: {CODE} -- nothing\n") == [
        Hit("a.py", 1, "unused")]


def test_a_test_s_home_path_leaves_its_mark_unused(no_names):
    path = "packages/a/tests/test_x.py"
    assert check_text(path, f"x = '/home/bob'  # noqa: {CODE} -- a sample\n") == [
        Hit(path, 1, "unused")]


def test_a_mark_quoted_as_inline_code_is_an_example_not_a_mark(no_names):
    assert check_text("a.md", "A genuine use carries `noqa: MX6 -- <why>`.\n") == []
    assert check_text("a.md", "/home/bob `# noqa: MX6 -- a sample`\n") == [
        Hit("a.md", 1, "home", "/home/bob")]


def test_a_json_line_takes_no_mark(no_names):
    source = f'{{"home": "/home/bob", "why": "noqa: {CODE} -- a sample"}}\n'
    assert check_text("a.json", source) == [Hit("a.json", 1, "home", "/home/bob")]


def test_a_line_with_two_rules_gives_one_line_each(names):
    source = cmd("ann: /home/bob /home/cy, brew| install x, dnf| install y\n")
    assert check_text("a.md", source) == [
        Hit("a.md", 1, "home", "/home/bob"), Hit("a.md", 1, "manager", cmd("brew| install")),
        Hit("a.md", 1, "name")]


def test_lines_are_counted_by_newlines_alone(no_names):
    source = "a\x0cb\r\n/home/bob\n"
    assert check_text("a.md", source) == [Hit("a.md", 2, "home", "/home/bob")]


# --- the lines ----------------------------------------------------------------------------

def test_each_hit_reads_as_path_line_and_fix():
    assert str(Hit("a.md", 3, "home", "/home/bob")) == (
        "a.md:3: machine text: home folder /home/bob — fix: write it from the home "
        "folder (~/...) or with an example user (/home/you), or mark a genuine use "
        f"# noqa: {CODE} -- <why>")
    assert str(Hit("a.md", 3, "manager", cmd("dnf| install"))) == (
        cmd("a.md:3: machine text: package manager dnf| install — fix: name what to install ")
        + "and leave the manager to the reader's platform, or mark a line written for one "
        f"platform # noqa: {CODE} -- <the platform>")
    assert str(Hit("a.md", 3, "name")) == (
        "a.md:3: machine text: private name — fix: reword it, or add the text to "
        "secrets' private_names.allowed if it is public on purpose")
    assert str(Hit("a.md", 3, "reason")) == (
        "a.md:3: machine text: the MX6 mark gives no reason; add -- <why> after MX6")
    assert str(Hit("a.md", 3, "unused")) == (
        "a.md:3: machine text: the MX6 mark lets nothing stand; remove it")


# --- the scan over a tree -----------------------------------------------------------------

def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    git(root, "init", "-q")
    (root / ".gitignore").write_text("ignored/\n")
    return root


def test_scan_reads_the_tree_and_leaves_the_skipped_files(repo, names):
    home = "/home/bob\n"
    for rel in (".github/workflows/ci.yml", "packages/moxi/src/moxi/_vendor/lib.py",
                "packages/a/src/moxi_a/web/page.js", "uv.lock", "package-lock.json",
                "packages/moxi-dev/renames.tsv", ".residue-allow", ".refs-allow",
                ".secrets-allow", ".public-paths", "LICENSE", "ignored/README.md"):
        write(repo, rel, home + "ann\n")
    write(repo, "packages/a/CHANGELOG.md", home)
    write(repo, "packages/a/web/src/page.ts", "// " + home)
    write(repo, "packages/a/tests/test_x.py", home + "x = 'ann'\n")
    write(repo, "tools/x.mjs", cmd("// brew| install x\n"))
    (repo / "packages/a/icon.png").write_bytes(b"\xff\xfe /home/bob \x00")
    git(repo, "add", "packages/a/CHANGELOG.md")
    assert scan(repo) == [
        Hit("packages/a/CHANGELOG.md", 1, "home", "/home/bob"),
        Hit("packages/a/tests/test_x.py", 2, "name"),
        Hit("packages/a/web/src/page.ts", 1, "home", "/home/bob"),
        Hit("tools/x.mjs", 1, "manager", cmd("brew| install")),
    ]
