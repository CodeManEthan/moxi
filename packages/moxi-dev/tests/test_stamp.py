"""`moxi-dev stamp` recomputes a web page's build stamp in Python and fails a stale one.

Most tests build a small repository under tmp whose stamps Python writes itself, the way
the Node build would. One test runs the real `tools/web/build.mjs` on such a repository
and checks that both sides compute the same stamp; it needs Node and the repository's
`npm ci`, and is skipped without them.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from moxi_dev import check, stamp

REPO = Path(__file__).resolve().parents[3]
VERSION = "0.28.2"


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
        cwd=root, check=True, capture_output=True, text=True).stdout


def write(root: Path, rel: str, data: str | bytes) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode()
    path.write_bytes(data)


def lock(version: str = VERSION) -> str:
    return json.dumps({"name": "w", "lockfileVersion": 3,
                       "packages": {"": {}, "node_modules/esbuild": {"version": version}}})


def page(root: Path, name: str) -> None:
    """A package with a page: build.json, a tsconfig and one source."""
    web = f"packages/{name}/web"
    write(root, f"{web}/build.json", '{"entries": {"page.js": "src/page.ts"}}\n')
    write(root, f"{web}/package.json", json.dumps({"name": f"moxi-{name}-web",
                                                    "private": True}) + "\n")
    write(root, f"{web}/tsconfig.json",
          '{"extends": "../../../tools/web/tsconfig.base.json", "include": ["src"]}\n')
    write(root, f"{web}/src/page.ts", f'export const name: string = "{name}";\n')


def rebuild(root: Path) -> None:
    """Write each package's stamp as the build would, from the working tree."""
    tree = stamp._WorkTree(root)
    version = stamp.locked_esbuild((root / stamp.LOCK).read_bytes())
    for name in stamp.web_packages(tree.paths):
        files = [(rel, (root / rel).read_bytes())
                 for rel in stamp.package_inputs(tree.paths, name)]
        write(root, stamp.stamp_path(name), stamp.stamp(version, files) + "\n")


@pytest.fixture
def repo(tmp_path):
    """Two packages with a page and one without, built and committed."""
    root = tmp_path / "r"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    shutil.copy(REPO / ".gitattributes", root / ".gitattributes")
    write(root, ".gitignore", "node_modules/\n")
    write(root, stamp.LOCK, lock())
    write(root, "tools/web/tsconfig.base.json", (REPO / "tools/web/tsconfig.base.json").read_text())
    write(root, "tools/web/client.d.ts", (REPO / "tools/web/client.d.ts").read_text())
    page(root, "alpha")
    page(root, "beta")
    write(root, "packages/plain/src/moxi_plain/__init__.py", "")
    rebuild(root)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "start")
    return root


def staged(root: Path) -> tuple[list[str], list[str]]:
    return stamp.check_stamps(root, staged=True)


# --- the hash and its inputs -------------------------------------------------------------

def test_the_inputs_are_web_sources_tools_and_the_lock():
    assert stamp.is_input("packages/ui/web/src/page.ts")
    assert stamp.is_input("tools/web/build.mjs")
    assert stamp.is_input("package-lock.json")
    assert not stamp.is_input("package.json")
    assert not stamp.is_input("packages/ui/src/moxi_ui/web/page.js")
    assert not stamp.is_input("packages/ui/web")


def test_a_package_reads_its_own_sources_and_the_shared_ones_in_byte_order():
    paths = ["tools/web/b.mjs", "packages/a/web/z.ts", "packages/a/web/ä.ts",
             "packages/a/web/Z.ts", "packages/b/web/a.ts", stamp.LOCK]
    assert stamp.package_inputs(paths, "a") == [
        stamp.LOCK, "packages/a/web/Z.ts", "packages/a/web/z.ts", "packages/a/web/ä.ts",
        "tools/web/b.mjs"]


def test_the_output_directory_is_the_import_package_web_dir():
    assert stamp.outdir("ui") == "packages/ui/src/moxi_ui/web"
    assert stamp.outdir("big-name") == "packages/big-name/src/moxi_big_name/web"


def test_the_stamp_covers_paths_bytes_order_and_the_esbuild_version():
    files = [("a", b"1"), ("b", b"2")]
    base = stamp.stamp("0.28.2", files)
    assert len(base) == 64
    assert stamp.stamp("0.28.3", files) != base
    assert stamp.stamp("0.28.2", [("a", b"1"), ("b", b"3")]) != base
    assert stamp.stamp("0.28.2", [("a", b"1"), ("c", b"2")]) != base
    assert stamp.stamp("0.28.2", [("a", b"1\0b"), ("", b"2")]) != base


def test_the_lock_must_pin_esbuild():
    assert stamp.locked_esbuild(lock("1.2.3").encode()) == "1.2.3"
    with pytest.raises(stamp.StampError, match="pins no esbuild"):
        stamp.locked_esbuild(b'{"packages": {}}')


# --- the working tree ---------------------------------------------------------------------

def test_a_fresh_build_passes(repo):
    assert stamp.check_stamps(repo) == (["alpha", "beta"], [])


def test_a_changed_source_fails_naming_the_build(repo):
    write(repo, "packages/alpha/web/src/page.ts", "export const name = 1;\n")
    names, problems = stamp.check_stamps(repo)
    assert names == ["alpha", "beta"]
    assert len(problems) == 1 and problems[0].startswith("alpha:")
    assert "npm run build" in problems[0]


def test_a_new_untracked_source_counts_before_it_is_staged(repo):
    write(repo, "packages/alpha/web/src/more.ts", "export {};\n")
    assert stamp.check_stamps(repo)[1]
    rebuild(repo)
    assert stamp.check_stamps(repo)[1] == []


def test_ignored_files_do_not_count(repo):
    write(repo, "packages/alpha/web/node_modules/x/index.js", "x\n")
    assert stamp.check_stamps(repo)[1] == []


def test_a_shared_input_changes_every_stamp(repo):
    write(repo, "tools/web/tsconfig.base.json", "{}\n")
    assert [p.split(":")[0] for p in stamp.check_stamps(repo)[1]] == ["alpha", "beta"]


def test_a_new_esbuild_changes_every_stamp(repo):
    write(repo, stamp.LOCK, lock("0.29.0"))
    assert len(stamp.check_stamps(repo)[1]) == 2


def test_a_missing_stamp_fails(repo):
    (repo / stamp.stamp_path("beta")).unlink()
    (problem,) = stamp.check_stamps(repo)[1]
    assert problem.startswith("beta:") and "missing" in problem


def test_an_input_git_may_rewrite_stops_the_check(repo):
    write(repo, "packages/alpha/web/NOTES.md", "notes\n")
    with pytest.raises(stamp.StampError, match="NOTES.md"):
        stamp.check_stamps(repo)


def test_a_linked_input_stops_the_check(repo):
    (repo / "packages/alpha/web/src/link.ts").symlink_to("page.ts")
    with pytest.raises(stamp.StampError, match="link"):
        stamp.check_stamps(repo)


def test_only_limits_the_packages(repo):
    write(repo, "packages/alpha/web/src/page.ts", "export const name = 1;\n")
    assert stamp.check_stamps(repo, only=["beta", "plain"]) == (["beta"], [])


# --- the index, as the pre-commit hook reads it -------------------------------------------

def test_a_staged_source_without_a_rebuild_is_refused(repo):
    write(repo, "packages/alpha/web/src/page.ts", "export const name = 2;\n")
    git(repo, "add", "-A")
    names, problems = staged(repo)
    assert names == ["alpha"]
    assert len(problems) == 1 and "npm run build" in problems[0]


def test_a_staged_source_with_its_rebuild_passes(repo):
    write(repo, "packages/alpha/web/src/page.ts", "export const name = 2;\n")
    rebuild(repo)
    git(repo, "add", "-A")
    assert staged(repo) == (["alpha"], [])


def test_the_index_is_read_not_the_working_tree(repo):
    write(repo, "packages/alpha/web/src/page.ts", "export const name = 2;\n")
    rebuild(repo)
    git(repo, "add", "-A")
    write(repo, "packages/alpha/web/src/page.ts", "export const name = 3;\n")
    assert staged(repo)[1] == []
    # And the other way: a rebuilt stamp left unstaged doesn't rescue a staged source.
    git(repo, "add", "packages/alpha/web/src/page.ts")
    rebuild(repo)
    assert staged(repo)[1]


def test_a_commit_that_touches_no_page_passes_whatever_the_pages_hold(repo):
    write(repo, "packages/alpha/web/src/page.ts", "export const name = 2;\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "a stale page, committed past the hook")
    write(repo, "packages/plain/src/moxi_plain/__init__.py", "x = 1\n")
    git(repo, "add", "-A")
    assert staged(repo) == ([], [])


def test_a_staged_shared_input_checks_every_page(repo):
    write(repo, "tools/web/tsconfig.base.json", "{}\n")
    git(repo, "add", "-A")
    names, problems = staged(repo)
    assert names == ["alpha", "beta"] and len(problems) == 2


def test_a_hand_edited_stamp_is_checked(repo):
    write(repo, stamp.stamp_path("beta"), "0" * 64 + "\n")
    git(repo, "add", "-A")
    assert staged(repo)[0] == ["beta"] and staged(repo)[1]


def test_the_first_commit_checks_every_page(tmp_path):
    root = tmp_path / "new"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    shutil.copy(REPO / ".gitattributes", root / ".gitattributes")
    write(root, stamp.LOCK, lock())
    page(root, "alpha")
    git(root, "add", "-A")
    names, problems = staged(root)
    assert names == ["alpha"] and "missing" in problems[0]


def test_main_refuses_with_the_fix_and_exits_1(repo, monkeypatch, capsys):
    write(repo, "packages/beta/web/src/page.ts", "export const name = 2;\n")
    git(repo, "add", "-A")
    monkeypatch.chdir(repo)
    assert stamp.main(["--staged"]) == 1
    out, err = capsys.readouterr()
    assert "npm run build" in err and "the commit is refused" in err
    assert out.strip() == "moxi-dev stamp: fail | packages: beta"


def test_main_passes_and_exits_0(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert stamp.main([]) == 0
    assert capsys.readouterr().out.strip() == "moxi-dev stamp: pass | packages: alpha, beta"


def test_main_exits_2_when_it_cannot_check(repo, monkeypatch, capsys):
    (repo / stamp.LOCK).unlink()
    monkeypatch.chdir(repo)
    assert stamp.main([]) == 2
    assert "npm install" in capsys.readouterr().err


# --- moxi-dev check runs it on the picked packages ----------------------------------------

def test_check_fails_a_stale_stamp_before_the_sync(repo, capsys):
    (repo / "pyproject.toml").write_text(
        '[tool.uv.workspace]\nmembers = ["packages/alpha", "packages/beta"]\n')
    for name in ("alpha", "beta"):
        (repo / f"packages/{name}/pyproject.toml").write_text(
            f'[project]\nname = "moxi-{name}"\nversion = "0.1.0"\n')
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "packages")
    git(repo, "switch", "-q", "-c", "work")
    write(repo, "packages/alpha/web/src/page.ts", "export const name = 2;\n")
    calls = []
    result = check.check(repo, environ={"PATH": "/usr/bin"},
                         run=lambda cmd, cwd, env: calls.append(cmd) or 0)
    assert result.code == 1
    assert calls == [["uv", "lock", "--check"]]
    assert "| packages: alpha | lock pass | stamps fail | sync not run |" in result.line()
    assert "npm run build" in capsys.readouterr().err
    rebuild(repo)
    result = check.check(repo, environ={"PATH": "/usr/bin"}, run=lambda cmd, cwd, env: 0)
    assert result.code == 0 and "stamps pass" in result.line()


# --- the Node build computes the same stamp -----------------------------------------------

NODE = shutil.which("node")
BUILT = (REPO / "node_modules" / "esbuild" / "package.json").is_file()


@pytest.mark.skipif(not (NODE and BUILT), reason="needs node and `npm ci` at the repository root")
def test_the_node_build_writes_the_stamp_python_computes(repo):
    write(repo, "tools/web/build.mjs", (REPO / "tools/web/build.mjs").read_bytes())
    write(repo, stamp.LOCK, (REPO / stamp.LOCK).read_bytes())
    # Names whose order differs between UTF-16 and UTF-8, a binary input and an
    # uppercase name, so the stamp's order and bytes are both exercised.
    write(repo, "packages/alpha/web/src/Zeta.ts", "export const z = 1;\n")
    write(repo, "packages/alpha/web/src/Ａ.ts", "export const a = 1;\n")
    write(repo, "packages/alpha/web/src/\U0001F600.ts", "export const e = 1;\n")
    write(repo, "packages/alpha/web/icon.png", bytes(range(256)))
    write(repo, "packages/alpha/web/build.json",
          '{"entries": {"page.js": "src/page.ts"}, "copy": ["icon.png"]}\n')
    assert stamp.check_stamps(repo)[1], "the sources changed, so the old stamp is stale"
    proc = subprocess.run([NODE, str(REPO / "tools/web/build.mjs"), "--root", str(repo)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    names, problems = stamp.check_stamps(repo)
    assert names == ["alpha", "beta"] and problems == []
    built = repo / stamp.outdir("alpha")
    assert sorted(p.name for p in built.iterdir()) == ["build-stamp.txt", "icon.png", "page.js"]
    git(repo, "add", "-A")
    assert stamp.check_stamps(repo, staged=True)[1] == []
