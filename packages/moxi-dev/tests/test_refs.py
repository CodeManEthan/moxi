import subprocess
import sys

from moxi_dev import refs


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout


def tree(tmp_path, files):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(root, "add", "-A")
    return root


def test_python_comments_and_docstrings_check_names_files_and_flags(tmp_path):
    root = tree(tmp_path, {
        "packages/a/src/a/main.py": ('"""Use `known`, `missing`, `made.py` and --gone."""\n'
                                      'known = "--ready"\nFILE = "made.py"\n'
                                      '# `known` and --ready\n'),
        "packages/a/src/a/made.py": "pass\n",
    })
    found = refs.scan(root)
    assert [(hit.text, hit.line) for hit in found] == [
        ("--gone", 1), ("missing", 1)]


def test_typescript_comments_are_read_but_urls_and_built_javascript_are_not(tmp_path):
    root = tree(tmp_path, {
        "packages/a/web/a.ts": ('const url = "https://outside.invalid";\n'
                                '// `MissingType` and --lost\nconst kept = "--ready";\n'),
        "packages/a/web/b.mjs": "/* `OtherMissing` */\n",
        "packages/a/web/built.js": "// `IgnoredMissing`\n",
    })
    assert {hit.text for hit in refs.scan(root)} == {"MissingType", "OtherMissing", "--lost"}


def test_an_allow_line_is_path_exact_and_must_stay_live(tmp_path):
    root = tree(tmp_path, {
        "packages/a/src/a/x.py": "# `OutsideName`\n",
        ".refs-allow": ("packages/a/src/a/x.py\tOutsideName\toutside API\n"
                        "packages/a/src/a/x.py\tGoneName\told exception\n"),
    })
    found = refs.scan(root)
    assert len(found) == 1 and found[0].stale and found[0].text == "GoneName"


def test_files_with_runtime_shapes_are_not_checked(tmp_path):
    root = tree(tmp_path, {
        "packages/a/src/a/x.py": "# `/run/x.py`, `~/x.ts`, `*.mjs`, `STATE.json`\n",
    })
    assert refs.scan(root) == []


def test_the_frozen_standard_library_contains_this_python():
    assert sys.stdlib_module_names <= refs.STDLIB_MODULE_NAMES
