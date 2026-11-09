import subprocess
import sys

from moxi_dev import naming


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


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


def test_keys_check_units_switches_types_and_skip_retired(tmp_path):
    root = tree(tmp_path, {
        "packages/a/src/moxi_a/settings.toml": '''
[wait]
type = "float"
doc = "Wait in seconds."
[old_seconds]
retired = "wait_s"
[feature.on]
type = "bool"
doc = "Whether it runs."
''',
        "packages/b/src/moxi_b/settings.toml": '''
[other.on]
type = "list"
doc = "Things to run."
''',
    })
    parts = [hit.part for hit in naming.scan(root)]
    assert parts.count("key unit") == 1
    assert parts.count("switch key") == 2
    assert parts.count("key type") == 1


def test_flags_every_verbs_table_and_marks_are_checked(tmp_path):
    root = tree(tmp_path, {
        "packages/a/src/moxi_a/cli.py": '''
import argparse
_VERBS = {"ls": object()}
VERBS = {}
VERBS.update({"state": object()})
def parser():
    p = argparse.ArgumentParser()
    p.add_argument("--wait", type=float)
    p.add_argument("--hold", metavar="SECONDS")
    p.add_parser("delete")
    return p
x = 1  # noqa
y = 2  # noqa: MX1
''',
    })
    found = naming.scan(root)
    assert len([hit for hit in found if hit.part == "CLI verb"]) == 3
    assert len([hit for hit in found if hit.part == "flag unit"]) == 2
    assert len([hit for hit in found if hit.part == "exception mark"]) == 2


def test_a_refused_verb_in_an_underscored_table_is_seen(tmp_path):
    root = tree(tmp_path, {"packages/a/src/moxi_a/cli.py": '_VERBS = {"ls": run}\n'})
    found = naming.scan(root)
    assert len(found) == 1 and found[0].message == "'ls' is a refused verb"


def test_public_numeric_parameters_include_amount_defaults_and_points(tmp_path):
    root = tree(tmp_path, {
        "packages/sample/src/moxi_sample/__init__.py": "",
        "packages/sample/src/moxi_sample/module.py": 'API = "moxi_sample.api"\n',
        "packages/sample/src/moxi_sample/api.py": '''
__all__ = ["wait", "Clock"]
def wait(timeout: float, deadline=30.0, expires: str = "24h", *, wait_s: float = 1):
    pass
class Clock:
    def poll(self, interval: int = 1):
        pass
''',
    })
    sys.modules.pop("moxi_sample", None)
    sys.modules.pop("moxi_sample.api", None)
    found = [hit for hit in naming.scan(root) if hit.part == "public parameter"]
    assert {word for hit in found for word in ("timeout", "deadline", "interval")
            if repr(word) in hit.message} == {"timeout", "deadline", "interval"}
    assert not any("expires" in hit.message or "wait_s" in hit.message for hit in found)


def test_a_settings_mark_goes_on_the_table_line(tmp_path):
    root = tree(tmp_path, {
        "packages/a/src/moxi_a/settings.toml": '''
[wait] # noqa: MX1
type = "float"
doc = "Wait in seconds."
''',
    })
    marks = [hit for hit in naming.scan(root) if hit.part == "exception mark"]
    assert len(marks) == 1 and marks[0].line == 2


def test_a_mark_with_a_reason_suppresses_the_rule_on_that_line(tmp_path):
    root = tree(tmp_path, {
        "packages/a/src/moxi_a/settings.toml": '''
[wait] # noqa: MX1 -- the value is a count, despite its historical name
type = "float"
doc = "Wait in seconds."
''',
    })
    assert naming.scan(root) == []


def test_a_public_parameter_mark_goes_on_the_definition_line(tmp_path):
    root = tree(tmp_path, {
        "packages/marked/src/moxi_marked/__init__.py": "",
        "packages/marked/src/moxi_marked/module.py": 'API = "moxi_marked.api"\n',
        "packages/marked/src/moxi_marked/api.py": '''
__all__ = ["collapse"]
def collapse(after: int = 1):  # noqa: MX1 -- after is a row count
    return after
''',
    })
    sys.modules.pop("moxi_marked", None)
    sys.modules.pop("moxi_marked.api", None)
    assert naming.scan(root) == []
