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


def test_a_repeated_allow_line_is_refused_and_a_malformed_one_says_so(tmp_path):
    root = tree(tmp_path, {
        "packages/a/src/a/x.py": "# `OutsideName`\n",
        ".refs-allow": ("packages/a/src/a/x.py\tOutsideName\toutside API\n"
                        "packages/a/src/a/x.py\tOutsideName\tsaid again\n"
                        "packages/a/src/a/x.py OutsideName no tabs\n"),
    })
    found = refs.scan(root)
    assert [(hit.line, hit.fault) for hit in found] == [(2, "duplicate"), (3, "malformed")]
    assert "repeats an earlier line" in str(found[0])
    assert "is not path<TAB>name<TAB>why" in str(found[1])


def test_builtins_and_standard_library_names_need_no_definition(tmp_path):
    root = tree(tmp_path, {
        "packages/a/src/a/x.py": "# `isinstance`, `shlex.quote` and `zoneinfo.ZoneInfo`\npass\n",
    })
    assert refs.scan(root) == []


def test_names_and_flags_in_tracked_data_files_count_as_defined(tmp_path):
    root = tree(tmp_path, {
        "packages/a/src/a/x.py": "# `made_in_data` and --from-data\npass\n",
        "packages/a/src/a/rows.toml": '[made_in_data]\nrun = "x --from-data"\n',
    })
    assert refs.scan(root) == []


def test_a_file_cite_is_found_through_a_code_string_naming_it(tmp_path):
    root = tree(tmp_path, {
        "packages/a/src/a/x.py": 'RUNNER = "bin/spawned_helper.py"\n# runs `spawned_helper.py`\n',
    })
    assert refs.scan(root) == []


def test_the_vendored_tree_is_not_checked(tmp_path):
    root = tree(tmp_path, {
        "packages/moxi/src/moxi/_vendor/lib.py": "# `VendoredMissing`\n",
    })
    assert refs.scan(root) == []
