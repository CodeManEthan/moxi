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

from moxi_dev import check, machine_text
from moxi_dev.machine_text import (CODE, Hit, SetUnavailable, check_text, is_scanned, is_test,
                                   manager_commands, scan, user_dirs)


def cmd(text: str) -> str:
    """`text` with each `|` removed: a command this file holds as data."""
    return text.replace("|", "")


#: A made-up private-names list: one word and two entries matched anywhere.
NAMES = ("ann", "~/vault", "work/acme")


def made_up_spans(text: str, path: str) -> list[tuple[int, int]]:
    """Where `text` holds a `NAMES` entry, no letter or digit at either end."""
    found = []
    for entry in NAMES:
        pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(entry) + r"(?![A-Za-z0-9])",
                             re.I)
        found += [match.span() for match in pattern.finditer(text)]
    return sorted(found)


@pytest.fixture
def names(monkeypatch):
    """The private-name rule reads `NAMES`, never the contributor's own list."""
    monkeypatch.setattr(machine_text, "private_spans", made_up_spans)


@pytest.fixture
def no_names(monkeypatch):
    monkeypatch.setattr(machine_text, "private_spans", lambda text, path: [])


# --- the home-folder rule ----------------------------------------------------------------

@pytest.mark.parametrize("line, found", [
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
    ("/mnt/c/Users/ann/x", "/mnt/c/Users/ann"),
    ("/mnt/d/users/Bo", "/mnt/d/users/Bo"),
    ("/home/jöran/x", "/home/jöran"),
    ("/home/Ølsen", "/home/Ølsen"),
    ("/Users/ñandu/Library", "/Users/ñandu"),
    ("C:\\Users\\Zoë\\AppData", "C:\\Users\\Zoë"),
    ("/home/ann.ünal/x", "/home/ann.ünal"),
    ("/home/1bob", "/home/1bob"),
    ("/Users/guest", "/Users/guest"),
    ("/Users/Publicity", "/Users/Publicity"),
    ("/home/Linuxbrew", "/home/Linuxbrew"),
    ("route('/home/settings')", "/home/settings"),
])
def test_a_user_dir_hits(line, found):
    assert user_dirs(line) == [found]


@pytest.mark.parametrize("line", [
    "/home/you/src/app", "/home/me2/x", "/home/USER", "/home/Name/x", "/home/username",
    "/Users/<name>/Library", "/Users/Shared/x", "/Users/Default", "C:\\Users\\Public",
    "C:\\Users\\All Users\\x", "%USERPROFILE%\\x", "C:\\Users\\%USERNAME%", "/home/$USER/x",
    "/home/${USER}/x", "/home/{user}/x", "/home/*/x", "/home/<you>", "docs/home/moxi",
    "/x/home/u", "~/home/u", "a.home/u", "/homebrew", "/home/", "~/.config/moxi",
    "~/code/app", "/root/.cache", "/opt/homebrew/bin", "XC:\\Users\\ann",
    "/Users/Guest/x", "/home/linuxbrew/.linuxbrew/bin", "c:\\users\\public",
    "C:\\USERS\\DEFAULT\\x", "c:/users/all users/x", "/mnt/c/Users/Public",
    "/mnt/c/users/default/x", "/mnt/c/Users/<you>", "/mnt/c/Users/you",
    "/home/_x", "x/mnt/c/Users/ann", "/mnt/cd/Users/ann",
])
def test_a_near_miss_is_no_user_dir(line):
    assert user_dirs(line) == []


def test_each_user_dir_on_a_line_is_found():
    assert user_dirs("/home/ann and /Users/bo") == ["/home/ann", "/Users/bo"]


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
    (cmd("/usr/bin/apt-get| install x"), cmd("apt-get| install")),
    (cmd("/opt/homebrew/bin/brew| install tmux"), cmd("brew| install")),
    (cmd("sudo /usr/bin/dnf| install x"), cmd("dnf| install")),
    (cmd("~/.linuxbrew/bin/brew| upgrade"), cmd("brew| upgrade")),
    (cmd("C:\\ProgramData\\chocolatey\\bin\\choco.exe| install git"), cmd("choco| install")),
    (cmd("winget.exe| install Git.Git"), cmd("winget| install")),
    (cmd("apt-get| dist-upgrade"), cmd("apt-get| dist-upgrade")),
    (cmd("apt| full-upgrade -y"), cmd("apt| full-upgrade")),
    (cmd("dnf| groupinstall 'Development Tools'"), cmd("dnf| groupinstall")),
    (cmd("yum| groupremove x"), cmd("yum| groupremove")),
    (cmd("$ brew| install tmux"), cmd("brew| install")),
    (cmd("% brew| install tmux"), cmd("brew| install")),
    (cmd("PS C:\\> winget| install x"), cmd("winget| install")),
    (cmd("me@box:~$ sudo apt-get| install x"), cmd("apt-get| install")),
    (cmd("# apt-get| install x"), cmd("apt-get| install")),
    (cmd("    // brew| install x"), cmd("brew| install")),
    (cmd("<!-- dnf| install x -->"), cmd("dnf| install")),
    (cmd(" * snap| install x"), cmd("snap| install")),
    (cmd("  scoop| install git"), cmd("scoop| install")),
    (cmd("cd x && apt-get| install y"), cmd("apt-get| install")),
    ("make || " + cmd("dnf| install y"), cmd("dnf| install")),
    (cmd("echo; yum| install x"), cmd("yum| install")),
    (cmd("start & choco| install x"), cmd("choco| install")),
    ("yes | " + cmd("pacman| -S x"), cmd("pacman| -S")),
    (cmd("DEBIAN_FRONTEND=noninteractive apt-get| install x"), cmd("apt-get| install")),
    (cmd("sudo -E apt-get| install x"), cmd("apt-get| install")),
    ("| " + cmd("`port| install tmux`") + " |", cmd("port| install")),
    (cmd('["sudo", "apt-get"|, "install"]'), cmd("apt-get| install")),
    (cmd('["/usr/bin/apt-get"|, "install"]'), cmd("apt-get| install")),
    (cmd("( 'apk'|, 'add', 'x')"), cmd("apk| add")),
])
def test_a_package_manager_command_hits(line, command):
    assert manager_commands(line) == [command]


@pytest.mark.parametrize("line", [
    "pip install x", "uv tool install moxi", "npm install", "an apt name",
    "install it with apt-get", 'fence.short / "brew" / "bin"', "/opt/homebrew/bin",
    'values["port"]', 'Socket("port", 8460)', "port 8460", "the support port",
    "Brew install", "xbrew install", "./brew install", "brew installer",
    "systemctl --user restart moxi", "launchctl load x", "pacman -s x",
    cmd("each port| update is logged"), cmd('("host", "port"|, "update")'),
    cmd("the port| search runs first"), cmd("a snap| info line"), cmd("the apk| add step"),
    cmd("install it with apt-get| install"), cmd("Run brew| install tmux."),
    cmd("bin/brew| install x"), cmd("x/usr/bin/apt-get| install"), cmd("CMD=apt-get| install"),
    cmd('"brew| install tmux (or port| install tmux)"'), cmd("- dnf| install x"),
    cmd("apt-get| dist-upgrader"), cmd("dnf| group install"),
])
def test_a_near_miss_is_no_command(line):
    assert manager_commands(line) == []


# --- the private-name rule ----------------------------------------------------------------

def test_a_private_name_hits_any_read_file_and_prints_no_name(names):
    text = "ask ann\nannual\nsee ~/vault/rounds\n"
    for path in ("packages/a/README.md", "packages/a/tests/test_x.py", "README.md"):
        assert check_text(path, text) == [Hit(path, 1, "name"), Hit(path, 3, "name")]
    assert "ann" not in str(Hit("a.md", 1, "name"))


@pytest.mark.parametrize("line, root", [
    ("cd /home/ann/src", "/home/"),
    ("/var/home/ann/.config", "/var/home/"),
    ("C:\\Users\\ann\\AppData", "C:\\Users\\"),
])
def test_a_user_dir_whose_user_is_a_private_name_prints_no_name(names, line, root):
    hits = check_text("a.md", line + "\n")
    assert hits == [Hit("a.md", 1, "home", root), Hit("a.md", 1, "name")]
    for hit in hits:
        assert "ann" not in str(hit)


def test_a_user_dir_on_a_line_with_a_private_name_prints_the_root_alone(names):
    hits = check_text("a.md", "ask ann about /home/bob/x\n")
    assert hits == [Hit("a.md", 1, "home", "/home/"), Hit("a.md", 1, "name")]
    for hit in hits:
        assert "ann" not in str(hit)
        assert "bob" not in str(hit)


def test_a_user_dir_on_a_line_with_no_private_name_prints_root_and_user(names):
    assert check_text("a.md", "ask ann\ncd /home/bob/x\n") == [
        Hit("a.md", 1, "name"), Hit("a.md", 2, "home", "/home/bob")]


@pytest.mark.parametrize("path, shown", [
    ("packages/a/docs/ann-setup.md", "packages/a/docs/<private name>"),
    ("packages/ann/README.md", "packages/<private name>/README.md"),
    ("packages/a/docs/ANN_x/x.ts", "packages/a/docs/<private name>/x.ts"),
    ("packages/work/acme/x.md", "packages/<private name>/<private name>/x.md"),
    ("packages/a/tests/test_ann.py", "packages/a/tests/<private name>"),
    ("annual/ann.txt", "annual/<private name>"),
])
def test_a_path_segment_holding_a_private_name_prints_hidden(names, path, shown):
    hits = check_text(path, "/home/bob\n" + cmd("brew| install x\n"))
    assert hits
    for hit in hits:
        assert hit.path == path
        assert str(hit).split(":")[0] == shown


@pytest.mark.parametrize("path", ["packages/a/docs/annual.md", "packages/acme/x.md",
                                  "packages/work/x.md", "README.md"])
def test_a_path_with_no_private_name_prints_as_it_is(names, path):
    assert str(check_text(path, cmd("brew| install x\n"))[0]).startswith(f"{path}:1:")


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
    hits = check_text("docs/ann-setup.md", "ask ann\n/home/bob\n")
    assert [str(hit).split(":")[0] for hit in hits] == ["docs/<private name>"] * 2
    config.write_text("[private_names\n", encoding="utf-8")
    with pytest.raises(SetUnavailable):
        check_text("a.md", text)


def test_a_list_secrets_can_t_read_stops_the_scan(repo, monkeypatch):
    write(repo, "README.md", "hello\n")
    unreadable_list(monkeypatch)
    with pytest.raises(SetUnavailable):
        scan(repo)
    assert machine_text.unreadable(SetUnavailable("no list")) == (
        "the machine-text step could not read your private-names list (no list); "
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
    assert not is_scanned(path)


@pytest.mark.parametrize("path", [
    "packages/a/CHANGELOG.md", "packages/a/web/src/page.ts", "packages/a/web/build.json",
    "packages/a/src/moxi_a/page.py", "packages/a/src/moxi_a/web.py", "packages/moxi/src/x.py",
    "packages/a/pyproject.toml", "packages/a/src/moxi_a/settings.toml", "README.md",
    "CONTRIBUTING.md", "tools/web/build.mjs", "packages/a/docs/x.md",
    "packages/a/scripts/x.sh",
])
def test_the_files_the_check_reads(path):
    assert is_scanned(path)


# --- the mark -----------------------------------------------------------------------------

def test_a_marked_line_lets_its_user_dir_and_command_stand(no_names):
    source = (f"a = '/home/bob'  # noqa: {CODE} -- one user's sample\n"
              + cmd(f"b = ['brew'|, 'install', 'x']  # noqa: {CODE} -- macOS\n")
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


def test_a_test_s_user_dir_leaves_its_mark_unused(no_names):
    path = "packages/a/tests/test_x.py"
    assert check_text(path, f"x = '/home/bob'  # noqa: {CODE} -- a sample\n") == [
        Hit(path, 1, "unused")]


def test_a_mark_quoted_as_inline_code_is_an_example_not_a_mark(no_names):
    assert check_text("a.md", "A genuine use carries `noqa: MX6 -- <why>`.\n") == []
    assert check_text("a.md", "/home/bob `# noqa: MX6 -- a sample`\n") == [
        Hit("a.md", 1, "home", "/home/bob")]


def test_a_mark_followed_by_inline_code_is_still_a_mark(no_names):
    doc = cmd(f"Run `brew| install x`. <!-- noqa: {CODE} -- written for macOS --> Then `y`.\n")
    assert check_text("a.md", doc) == []
    doc = f"(`C:\\Users\\bot`, noqa: {CODE} -- the Windows runner) or `C:\\Users\\x`\n"
    assert check_text("a.md", doc) == []


def test_a_comment_s_end_is_no_reason(no_names):
    doc = f"/home/bob <!-- noqa: {CODE} -- -->\n"
    assert check_text("a.md", doc) == [
        Hit("a.md", 1, "home", "/home/bob"), Hit("a.md", 1, "reason")]
    web = f"const p = '/Users/bo';  /* noqa: {CODE} -- */\n"
    assert check_text("a.ts", web) == [
        Hit("a.ts", 1, "home", "/Users/bo"), Hit("a.ts", 1, "reason")]


def test_a_json_line_takes_no_mark(no_names):
    source = f'{{"home": "/home/bob", "why": "noqa: {CODE} -- a sample"}}\n'
    assert check_text("a.json", source) == [Hit("a.json", 1, "home", "/home/bob")]


def test_a_line_with_two_rules_gives_one_line_each(names):
    source = cmd("ann: /home/bob /home/cy; brew| install x; dnf| install y\n")
    assert check_text("a.md", source) == [
        Hit("a.md", 1, "home", "/home/"), Hit("a.md", 1, "manager", cmd("brew| install")),
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
    user_dir = "/home/bob\n"
    for rel in (".github/workflows/ci.yml", "packages/moxi/src/moxi/_vendor/lib.py",
                "packages/a/src/moxi_a/web/page.js", "uv.lock", "package-lock.json",
                "packages/moxi-dev/renames.tsv", ".residue-allow", ".refs-allow",
                ".secrets-allow", ".public-paths", "LICENSE", "ignored/README.md"):
        write(repo, rel, user_dir + "ann\n")
    write(repo, "packages/a/CHANGELOG.md", user_dir)
    write(repo, "packages/a/web/src/page.ts", "// " + user_dir)
    write(repo, "packages/a/tests/test_x.py", user_dir + "x = 'ann'\n")
    write(repo, "tools/x.mjs", cmd("// brew| install x\n"))
    (repo / "packages/a/icon.png").write_bytes(b"\xff\xfe /home/bob \x00")
    git(repo, "add", "packages/a/CHANGELOG.md")
    assert scan(repo) == [
        Hit("packages/a/CHANGELOG.md", 1, "home", "/home/bob"),
        Hit("packages/a/tests/test_x.py", 2, "name"),
        Hit("packages/a/web/src/page.ts", 1, "home", "/home/bob"),
        Hit("tools/x.mjs", 1, "manager", cmd("brew| install")),
    ]


def test_scan_numbers_lines_as_git_does(repo, no_names):
    write(repo, "a.md", "")
    (repo / "a.md").write_bytes(b"one\rtwo\x0c\xc2\x85\n/home/bob\r\nthree\r\n/Users/ann\n")
    (repo / "b.md").write_bytes(b"\r\n\r\n/home/bob\r\n")
    assert scan(repo) == [Hit("a.md", 2, "home", "/home/bob"),
                          Hit("a.md", 4, "home", "/Users/ann"),
                          Hit("b.md", 3, "home", "/home/bob")]


# --- in the check -------------------------------------------------------------------------

def steps(root: Path) -> tuple[check.Result, check._Steps]:
    result = check.Result()
    return result, check._Steps(root, [], (), {}, lambda *a: 0, lambda *a: [], result)


def test_the_static_steps_run_the_machine_step_after_a_failed_pronoun_step(
        repo, names, monkeypatch, capsys):
    write(repo, "packages/a/README.md", "Ask " + "him.\n/home/bob/x\n")
    for name in ("tags", "references", "names", "ruff", "renames", "release"):
        monkeypatch.setattr(check._Steps, name, lambda self: True)
    result, static = steps(repo)
    assert static.static() is False
    assert result.pronouns == "fail" and result.machine == "fail"
    assert "| pronouns fail | machine fail | stamps not run |" in result.line()
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 2 and "machine text" not in out[0]
    assert out[1] == f"moxi-dev check: {Hit('packages/a/README.md', 2, 'home', '/home/bob')}"


def test_the_machine_step_passes_a_clean_tree(repo, names):
    write(repo, "packages/a/README.md", "See ~/code/app.\n")
    result, static = steps(repo)
    assert static.machine() is True and result.machine == "pass"


def test_only_machine_runs_the_step_alone_and_filters_by_path(repo, names, capsys):
    write(repo, "packages/a/README.md", "/home/bob\n")
    write(repo, "packages/b/README.md", "ask ann\n")
    assert check.only(repo, "machine", ["packages/b"]) == 1
    out = capsys.readouterr().out
    assert out == f"{Hit('packages/b/README.md', 1, 'name')}\n"
    assert check.only(repo, "machine", ["packages/c"]) == 0


def unreadable_list(monkeypatch):
    def unreadable(text, path):
        raise SetUnavailable("private_names.words: not a list")

    monkeypatch.setattr(machine_text, "private_spans", unreadable)


def test_only_machine_filters_by_the_real_path_and_prints_it_hidden(repo, names, capsys):
    write(repo, "packages/ann/README.md", "/home/bob\n")
    assert check.only(repo, "machine", ["packages/ann"]) == 1
    out = capsys.readouterr().out
    assert out.startswith("packages/<private name>/README.md:1: machine text: home folder")
    assert check.only(repo, "machine", ["packages/b"]) == 0


def test_a_list_secrets_can_t_read_exits_2_from_the_check(repo, monkeypatch, capsys):
    write(repo, "pyproject.toml", "[tool.uv.workspace]\nmembers = []\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q",
        "-m", "start")
    for name in ("tags", "references", "names", "ruff", "renames", "release"):
        monkeypatch.setattr(check._Steps, name, lambda self: True)
    unreadable_list(monkeypatch)
    result = check.check(repo, static=True)
    assert result.code == 2 and result.machine == "not run"
    assert capsys.readouterr().err == (
        "moxi-dev check: the machine-text step could not read your private-names list "
        "(private_names.words: not a list); run moxi config secrets check\n")


def test_a_list_secrets_can_t_read_exits_2_from_only_machine(repo, monkeypatch, capsys):
    write(repo, "README.md", "hello\n")
    unreadable_list(monkeypatch)
    assert check.only(repo, "machine") == 2
    assert capsys.readouterr().err == (
        "moxi-dev check: the machine-text step could not read your private-names list "
        "(private_names.words: not a list); run moxi config secrets check\n")
