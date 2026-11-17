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


def test_an_enabled_key_must_be_a_bool(tmp_path):
    root = tree(tmp_path, {
        "packages/a/src/moxi_a/settings.toml": '[part.enabled]\ntype = "int"\ndoc = "How many."\n',
    })
    [hit] = naming.scan(root)
    assert (hit.code, hit.part) == ("MX4", "switch key") and "has type 'int'" in hit.message


def test_non_house_units_are_hit_naming_the_house_suffix(tmp_path):
    root = tree(tmp_path, {
        "packages/a/src/moxi_a/settings.toml": '[hold_seconds]\ntype = "float"\ndoc = "A hold."\n',
        "packages/a/src/moxi_a/cli.py": '''
def parser(p):
    p.add_argument("--hold-secs", type=float)
    p.add_argument("--size-mb", type=int)
    p.add_argument("--grace", metavar="MINUTES")
    p.add_argument("--retry", type=float)
''',
        "packages/a/src/moxi_a/__init__.py": "",
        "packages/a/src/moxi_a/module.py": 'API = "moxi_a.api"\n',
        "packages/a/src/moxi_a/api.py": '''
__all__ = ["hold"]
def hold(pause_millis: int = 5):
    pass
''',
    })
    sys.modules.pop("moxi_a", None)
    sys.modules.pop("moxi_a.api", None)
    fixes = {(hit.part, hit.message.split()[0], hit.fix) for hit in naming.scan(root)}
    assert fixes == {
        ("key unit", "hold_seconds", "end the key with _s"),
        ("flag unit", "--hold-secs", "end the flag with -s"),
        ("flag unit", "--size-mb", "end the flag with -mib"),
        ("flag unit", "--grace", "end the flag with -minutes"),
        ("flag unit", "--retry", "end the flag with -s, -ms, -minutes, -hours or -days"),
        ("public parameter", "moxi_a.api.hold", "end the parameter with _ms"),
    }


def test_a_retired_key_is_not_read(tmp_path):
    root = tree(tmp_path, {
        "packages/a/src/moxi_a/settings.toml": '''
[old_seconds]
type = "int"
doc = "Wait in seconds."
retired = "renamed: set wait_s instead"
[wait_s]
type = "float"
doc = "Wait in seconds."
[other.old_seconds]
type = "float"
retired = "renamed: set other.wait_s instead"
''',
    })
    assert naming.scan(root) == []


def test_names_a_module_getattr_serves_are_read(tmp_path):
    root = tree(tmp_path, {
        "packages/lazy/src/moxi_lazy/__init__.py": "",
        "packages/lazy/src/moxi_lazy/module.py": 'API = "moxi_lazy.api"\n',
        "packages/lazy/src/moxi_lazy/_impl.py": "def wait(timeout: float):\n    pass\n",
        "packages/lazy/src/moxi_lazy/api.py": '''
_SERVED = {"wait": "moxi_lazy._impl"}
def __getattr__(name):
    if name in _SERVED:
        import importlib
        return getattr(importlib.import_module(_SERVED[name]), name)
    raise AttributeError(name)
''',
    })
    for name in ("moxi_lazy", "moxi_lazy.api", "moxi_lazy._impl"):
        sys.modules.pop(name, None)
    [hit] = naming.scan(root)
    assert hit.part == "public parameter" and "'timeout'" in hit.message


def test_a_mark_on_a_decorated_definitions_def_line_suppresses_its_hit(tmp_path):
    root = tree(tmp_path, {
        "packages/deco/src/moxi_deco/__init__.py": "",
        "packages/deco/src/moxi_deco/module.py": 'API = "moxi_deco.api"\n',
        "packages/deco/src/moxi_deco/api.py": '''
__all__ = ["collapse", "Box"]
def deco(f):
    return f
@deco
def collapse(after: int = 1):  # noqa: MX1 -- after is a row count
    return after
class Box:
    @staticmethod
    def shut(after: int = 1):  # noqa: MX1 -- a count
        return after
''',
    })
    sys.modules.pop("moxi_deco", None)
    sys.modules.pop("moxi_deco.api", None)
    assert naming.scan(root) == []


def test_an_api_module_that_does_not_import_is_a_hit(tmp_path):
    root = tree(tmp_path, {
        "packages/broken/src/moxi_broken/__init__.py": "",
        "packages/broken/src/moxi_broken/module.py": 'API = "moxi_broken.api"\n',
        "packages/broken/src/moxi_broken/api.py": "raise RuntimeError('no')\n",
    })
    sys.modules.pop("moxi_broken", None)
    [hit] = naming.scan(root)
    assert hit.path == "packages/broken/src/moxi_broken/module.py" and "did not import" in hit.message


def test_env_mappings_use_env(tmp_path):
    root = tree(tmp_path, {"packages/a/src/moxi_a/one.py": '''
import os
def read(environment, env):
    child_environ = os.environ.copy()
    return environment, env, child_environ, os.environ
'''})
    hits = [hit for hit in naming.scan(root) if hit.part == "environment mapping"]
    assert {hit.message.split("'")[1] for hit in hits} == {"environment", "child_environ"}
    assert all(hit.fix == "use env" for hit in hits)


def test_caught_exceptions_use_exc_and_nested_handlers_name_their_exception(tmp_path):
    root = tree(tmp_path, {"packages/a/src/moxi_a/one.py": '''
def read():
    try:
        pass
    except ValueError as error:
        try:
            pass
        except TypeError as parse_exc:
            pass
    try:
        pass
    except OSError as exc:
        pass
'''})
    hits = [hit for hit in naming.scan(root) if hit.part == "caught exception"]
    assert len(hits) == 1 and "'error'" in hits[0].message
    assert "<what>_exc" in hits[0].fix


def test_dirs_name_what_dir_they_hold(tmp_path):
    root = tree(tmp_path, {"packages/a/src/moxi_a/one.py": '''
def read(cards_folder, cards_dir):
    archive_directory = cards_dir
    return cards_folder, archive_directory
'''})
    hits = [hit for hit in naming.scan(root) if hit.part == "directory"]
    assert {hit.message.split("'")[1] for hit in hits} == {"cards_folder", "archive_directory"}
    assert all("<what>_dir" in hit.fix for hit in hits)


def test_open_files_use_fh_but_urlopen_and_popen_are_not_files(tmp_path):
    root = tree(tmp_path, {"packages/a/src/moxi_a/one.py": '''
def read(path, opener, urlopen, popen):
    with open(path) as stream:
        pass
    with opener.open(path) as state_fh:
        pass
    with open(path) as fh:
        pass
    with urlopen(path) as response:
        pass
    with popen(path) as process:
        pass
'''})
    hits = [hit for hit in naming.scan(root) if hit.part == "open file"]
    assert len(hits) == 1 and "'stream'" in hits[0].message
    assert "<what>_fh" in hits[0].fix


def test_database_connections_use_conn_but_ipc_connect_is_a_client(tmp_path):
    root = tree(tmp_path, {"packages/a/src/moxi_a/one.py": '''
import sqlite3
from moxi import ipc, store
def read(database: sqlite3.Connection, conn: sqlite3.Connection | None = None):
    database_handle = sqlite3.connect(":memory:")
    with store.connect("x") as store_handle:
        pass
    client = ipc.connect("x")
    return database, conn, database_handle, store_handle, client
'''})
    hits = [hit for hit in naming.scan(root) if hit.part == "database connection"]
    assert {hit.message.split("'")[1] for hit in hits} == {
        "database", "database_handle", "store_handle"}
    assert all("<what>_conn" in hit.fix for hit in hits)


def test_print_callbacks_use_say(tmp_path):
    root = tree(tmp_path, {"packages/a/src/moxi_a/one.py": '''
def emit(callback=print, *, say=print):
    return callback, say
def call(target):
    target(reporter=print)
    target(say=print)
'''})
    hits = [hit for hit in naming.scan(root) if hit.part == "print callback"]
    assert len(hits) == 2
    assert {hit.fix for hit in hits} == {"use say", "name the callback say"}


def test_actor_parameters_use_by_but_stored_fields_keep_actor(tmp_path):
    root = tree(tmp_path, {"packages/a/src/moxi_a/one.py": '''
from dataclasses import dataclass
@dataclass
class Launch:
    actor: str
def record(actor, by):
    return Launch(actor), by
'''})
    hits = [hit for hit in naming.scan(root) if hit.part == "actor parameter"]
    assert len(hits) == 1 and hits[0].fix.startswith("use by")


def test_env_constants_have_one_owner_name_and_binding(tmp_path):
    root = tree(tmp_path, {
        "packages/sample/src/moxi_sample/vars.py": '''
"""MOXI_SAMPLE_DOC"""
import os
GOOD_ENV = "MOXI_SAMPLE_GOOD"
BAD = "MOXI_SAMPLE_BAD"
FIRST_ENV = "MOXI_SAMPLE_DUP"
SECOND_ENV = "MOXI_SAMPLE_DUP"
DECLARED = {"MOXI_SAMPLE_MEMBER"}
value = os.getenv("MOXI_SAMPLE_INLINE")
''',
        "packages/sample/src/moxi_sample/module.py": '''
import os
value = os.getenv("MOXI_SAMPLE_ANNOUNCEMENT")
''',
        "packages/other/src/moxi_other/vars.py": '''
FOREIGN_ENV = "MOXI_SAMPLE_FOREIGN"
''',
        "packages/moxi-dev/src/moxi_dev/vars.py": 'TEST_ENV = "MOXI_TEST_ONE"\n',
        "packages/moxi/src/moxi/vars.py": 'CORE_ENV = "MOXI_CORE_ONE"\n',
        "packages/sample/tests/test_vars.py": 'NAME = "MOXI_SAMPLE_TEST"\n',
    })
    hits = [hit for hit in naming.scan(root) if hit.code == "MX2"]
    assert {(hit.part, hit.message.split()[0]) for hit in hits} == {
        ("environment constant", "BAD"),
        ("environment constant duplicate", "SECOND_ENV"),
        ("environment constant owner", "FOREIGN_ENV"),
        ("environment literal", "MOXI_SAMPLE_INLINE"),
    }


def test_checked_marks_need_a_reason(tmp_path):
    root = tree(tmp_path, {"packages/a/src/moxi_a/one.py": '''
def one():  # noqa: N802
    pass
def two():  # noqa: A002 -- an outside interface fixes the name
    pass
def three():  # noqa: FBT001
    pass
'''})
    hits = [hit for hit in naming.scan(root) if hit.part == "exception mark"]
    assert len(hits) == 2 and {hit.line for hit in hits} == {2, 6}
