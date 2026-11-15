import subprocess
from pathlib import Path

import pytest

from moxi_dev import rename


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout


def repo(tmp_path, files):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Test")
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "start")
    return root


def table(tmp_path, *rows):
    path = tmp_path / "renames.tsv"
    path.write_text("# rows\n" + "".join("\t".join(row) + "\n" for row in rows))
    return path


def row(path, scope, kind, old, new, package="p"):
    return package, path, scope, kind, old, new


def test_name_resolves_aliases_imports_reexports_and_special_strings(tmp_path):
    root = repo(tmp_path, {
        "packages/p/src/p/impl.py": '''
def old(value):
    """Call `old()`."""
    return value
''',
        "packages/p/src/p/api.py": '''
from .impl import old
__all__ = ["old"]
def __getattr__(name):
    if name == "old":
        return old
''',
        "packages/q/src/q/use.py": '''
import p.impl as impl
from p.impl import old as direct
from p.api import old
a = impl.old(1)
b = direct(2)
c = old(3)
''',
    })
    path = "packages/p/src/p/impl.py"
    renames = table(tmp_path, row(path, "-", "name", "old", "fresh"))
    assert rename.run(root, renames) == 0
    assert "def fresh" in (root / path).read_text()
    assert "`fresh()`" in (root / path).read_text()
    assert 'from .impl import fresh' in (root / "packages/p/src/p/api.py").read_text()
    assert '__all__ = ["fresh"]' in (root / "packages/p/src/p/api.py").read_text()
    use = (root / "packages/q/src/q/use.py").read_text()
    assert "impl.fresh(1)" in use and "direct(2)" in use and "fresh(3)" in use
    before = {p: (root / p).read_text() for p in (path, "packages/p/src/p/api.py",
                                                  "packages/q/src/q/use.py")}
    assert rename.run(root, renames) == 0
    assert before == {p: (root / p).read_text() for p in before}


def test_rows_report_to_apply_applied_and_not_here_before_editing(tmp_path, capsys):
    root = repo(tmp_path, {"packages/p/src/p/x.py": "old = 1\ndone = 2\n"})
    renames = table(
        tmp_path,
        row("packages/p/src/p/x.py", "-", "name", "old", "new"),
        row("packages/p/src/p/x.py", "-", "name", "before", "done"),
        row("packages/p/src/p/x.py", "-", "name", "gone", "absent"),
    )
    assert rename.run(root, renames, dry_run=True) == 0
    assert "1 to apply, 1 applied, 1 not here" in capsys.readouterr().out
    assert (root / "packages/p/src/p/x.py").read_text() == "old = 1\ndone = 2\n"


def test_name_row_renames_an_exception_alias_and_its_reads(tmp_path):
    path = "packages/p/src/p/x.py"
    root = repo(tmp_path, {path: '''
def read():
    try:
        return open("missing")
    except OSError as error:
        return str(error)
'''})
    renames = table(tmp_path, row(path, "read", "name", "error", "exc"))

    assert rename.run(root, renames) == 0
    text = (root / path).read_text()
    assert "except OSError as exc:" in text
    assert "return str(exc)" in text


def test_use_changes_one_exact_line_and_keep_covers_an_unrelated_old_name(tmp_path):
    source = "def fresh():\n    pass\nold()\noutside.old()\n"
    root = repo(tmp_path, {"packages/p/src/p/x.py": source})
    renames = table(
        tmp_path,
        row("packages/p/src/p/x.py", "-", "name", "old", "fresh"),
        row("packages/p/src/p/x.py", "old()", "use", "old", "fresh"),
        row("packages/p/src/p/x.py", "outside.old()", "keep", "old", "fresh"),
    )
    assert rename.run(root, renames) == 0
    text = (root / "packages/p/src/p/x.py").read_text()
    assert "fresh()\noutside.old()" in text
    assert rename.run(root, renames) == 0


def test_each_leftover_kind_is_reported(tmp_path, capsys):
    root = repo(tmp_path, {
        "packages/p/src/p/x.py": '''
def fresh(timeout):
    pass
unknown(timeout=1)
old()
thing.old
lookup = "old"
''',
        "packages/q/src/q/note.py": "# `old`\n",
    })
    renames = table(tmp_path,
                    row("packages/p/src/p/x.py", "-", "name", "old", "fresh"),
                    row("packages/p/src/p/x.py", "fresh", "name", "timeout", "timeout_s"))
    assert rename.run(root, renames, dry_run=True) == 1
    out = capsys.readouterr().out
    for phrase in ("unresolved call still passes keyword", "unresolved call still uses",
                   "unresolved attribute", "string lookup", "comment still cites"):
        assert phrase in out


def test_kwonly_constructor_rewrites_resolved_calls_and_flags_unresolved_ones(tmp_path,
                                                                              capsys):
    root = repo(tmp_path, {
        "packages/p/src/p/model.py": '''
class Cls:
    def __init__(self, value, dim):
        self.value = value
        self.dim = dim
good = Cls(1, 2)
''',
        "packages/q/src/q/use.py": "from p.model import Cls\nother = Cls(3, 4)\n",
        "packages/q/src/q/unknown.py": "def f(Cls):\n    return Cls(5, 6)\n",
    })
    renames = table(tmp_path,
                    row("packages/p/src/p/model.py", "Cls.__init__", "kwonly", "dim", "dim"))
    assert rename.run(root, renames, dry_run=True) == 1
    assert "unresolved Cls call" in capsys.readouterr().out
    keep = row("packages/q/src/q/unknown.py", "return Cls(5, 6)", "keep", "dim", "dim")
    renames = table(tmp_path,
                    row("packages/p/src/p/model.py", "Cls.__init__", "kwonly", "dim", "dim"),
                    keep)
    assert rename.run(root, renames) == 0
    assert "*, dim" in (root / "packages/p/src/p/model.py").read_text()
    assert "Cls(1, dim=2)" in (root / "packages/p/src/p/model.py").read_text()
    assert "Cls(3, dim=4)" in (root / "packages/q/src/q/use.py").read_text()


def test_a_local_bound_from_a_resolved_class_reaches_its_method(tmp_path):
    root = repo(tmp_path, {
        "packages/p/src/p/client.py": '''
class Client:
    def call(self, timeout):
        return timeout
def use():
    client = Client()
    return client.call(timeout=1)
''',
    })
    renames = table(tmp_path,
                    row("packages/p/src/p/client.py", "Client.call", "name",
                        "timeout", "timeout_s"))
    assert rename.run(root, renames) == 0
    text = (root / "packages/p/src/p/client.py").read_text()
    assert "def call(self, timeout_s)" in text
    assert "client.call(timeout_s=1)" in text


def test_external_resolved_keywords_are_neither_changed_nor_leftovers(tmp_path):
    root = repo(tmp_path, {
        "packages/p/src/p/x.py": "import subprocess\ndef fresh(timeout): pass\nsubprocess.run([], timeout=1)\n",
    })
    renames = table(tmp_path,
                    row("packages/p/src/p/x.py", "fresh", "name", "timeout", "timeout_s"))
    assert rename.run(root, renames) == 0
    assert "subprocess.run([], timeout=1)" in (root / "packages/p/src/p/x.py").read_text()


def test_main_comparison_reports_a_move_and_a_row_at_the_new_place_clears_it(tmp_path,
                                                                             capsys):
    path = "packages/p/src/p/old.py"
    root = repo(tmp_path, {path: "def new(value):\n    return value\n"})
    main_ref = git(root, "rev-parse", "HEAD").strip()
    (root / path).unlink()
    moved = root / "packages/p/src/p/moved.py"
    moved.write_text("def old(value):\n    return value\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "move")
    original = row(path, "-", "name", "old", "new")
    renames = table(tmp_path, original)
    assert rename.run(root, renames, dry_run=True, main_ref=main_ref) == 1
    assert "moved here" in capsys.readouterr().out
    clearing = row("packages/p/src/p/moved.py", "-", "name", "old", "new")
    renames = table(tmp_path, original, clearing)
    assert rename.run(root, renames, dry_run=True, main_ref=main_ref) == 0


def test_main_comparison_says_when_it_did_not_run(tmp_path, capsys):
    root = repo(tmp_path, {"packages/p/src/p/x.py": "pass\n"})
    renames = table(tmp_path)
    assert rename.run(root, renames, dry_run=True) == 0
    assert "comparison with main did not run" in capsys.readouterr().out
    git(root, "checkout", "--orphan", "other")
    other = git(root, "rev-parse", "main").strip()
    # The old main commit is not an ancestor of an unborn orphan branch.
    assert rename.run(root, renames, dry_run=True, main_ref=other) == 0
    assert "not an ancestor" in capsys.readouterr().out


def test_removed_scope_keep_form_is_already_applied(tmp_path):
    root = repo(tmp_path, {"packages/p/src/p/x.py": "pass\n"})
    renames = table(tmp_path,
                    row("-", "removed:p.x:gone", "keep", "old", "new"))
    project = rename.Project(root)
    assert rename.state(project, rename.read_table(renames)[0]) == "applied"


def test_a_field_row_renames_constructor_keywords_and_self_reads(tmp_path):
    root = repo(tmp_path, {
        "packages/p/src/p/model.py": '''
from dataclasses import dataclass
@dataclass
class Clock:
    age_seconds: float = 0.0
    def older(self):
        return self.age_seconds + 1
''',
        "packages/p/src/p/use.py": "from p.model import Clock\nmade = Clock(age_seconds=3)\n",
    })
    renames = table(tmp_path,
                    row("packages/p/src/p/model.py", "Clock", "name", "age_seconds", "age_s"))
    assert rename.run(root, renames) == 0
    model = (root / "packages/p/src/p/model.py").read_text()
    assert "    age_s: float" in model and "self.age_s + 1" in model
    assert "Clock(age_s=3)" in (root / "packages/p/src/p/use.py").read_text()


def test_kwonly_names_every_later_positional_argument_and_drops_the_old_star(tmp_path):
    root = repo(tmp_path, {
        "packages/p/src/p/git.py": '''
def changes(root, staged=True, rng=None, *, paths=()):
    return root
''',
        "packages/p/src/p/use.py": "from p.git import changes\nchanges('.', False, 'a..b')\n",
    })
    renames = table(tmp_path,
                    row("packages/p/src/p/git.py", "changes", "kwonly", "staged", "staged"))
    assert rename.run(root, renames) == 0
    text = (root / "packages/p/src/p/git.py").read_text()
    assert "def changes(root, *, staged=True, rng=None, paths=()):" in text
    assert "changes('.', staged=False, rng='a..b')" in (
        root / "packages/p/src/p/use.py").read_text()


def test_all_strings_change_only_inside_all(tmp_path):
    root = repo(tmp_path, {"packages/p/src/p/paths.py": '''
HOME = "P_HOME"
LOCATORS = ("HOME", "DATA_HOME")
__all__ = ["HOME", "LOCATORS"]
'''})
    renames = table(tmp_path, row("packages/p/src/p/paths.py", "-", "name", "HOME", "HOME_ENV"),
                    row("packages/p/src/p/paths.py", 'LOCATORS = ("HOME", "DATA_HOME")',
                        "keep", "HOME", "HOME"))
    assert rename.run(root, renames) == 0
    text = (root / "packages/p/src/p/paths.py").read_text()
    assert 'LOCATORS = ("HOME", "DATA_HOME")' in text and '__all__ = ["HOME_ENV",' in text


def test_a_parameter_cite_changes_only_inside_its_function(tmp_path, capsys):
    root = repo(tmp_path, {"packages/p/src/p/x.py": '''
"""`run` waits `timeout` seconds."""
def run(timeout=1):
    """Give up after `timeout` seconds."""
def other(timeout=2):
    """Its own `timeout`."""
'''})
    renames = table(tmp_path, row("packages/p/src/p/x.py", "run", "name", "timeout", "timeout_s"))
    assert rename.run(root, renames) == 1
    text = (root / "packages/p/src/p/x.py").read_text()
    assert "after `timeout_s` seconds" in text and "Its own `timeout`" in text
    assert "waits `timeout` seconds" in text
    assert "x.py:2: comment still cites 'timeout'" in capsys.readouterr().out


def test_a_keep_row_stops_a_cite_rewrite_on_its_line(tmp_path):
    root = repo(tmp_path, {"packages/p/src/p/paths.py": '''
"""The home is `HOME`, or `HOME`'s value."""
HOME = "P_HOME"
x = HOME
'''})
    renames = table(tmp_path, row("packages/p/src/p/paths.py", "-", "name", "HOME", "HOME_ENV"),
                    row("packages/p/src/p/paths.py", '"""The home is `HOME`, or `HOME`\'s value."""',
                        "keep", "HOME", "HOME"))
    assert rename.run(root, renames) == 0
    text = (root / "packages/p/src/p/paths.py").read_text()
    assert "The home is `HOME`" in text and "x = HOME_ENV" in text


def test_an_instance_attribute_chain_is_unresolved_and_a_parameter_is_never_one(tmp_path,
                                                                                capsys):
    root = repo(tmp_path, {"packages/p/src/p/x.py": '''
class Holder:
    def __init__(self):
        self.inner = object()
    def read(self):
        return self.inner.marker
marker = 1
def fn(wait=1):
    return wait
thing.wait
thing.wait()
'''})
    renames = table(tmp_path, row("packages/p/src/p/x.py", "-", "name", "marker", "mark"),
                    row("packages/p/src/p/x.py", "fn", "name", "wait", "wait_s"))
    assert rename.run(root, renames, dry_run=True) == 1
    out = capsys.readouterr().out
    assert "x.py:6: unresolved attribute still uses 'marker'" in out
    assert "'wait'" not in out


def test_a_run_that_would_not_parse_writes_nothing(tmp_path):
    source = "def fn(wait=1):\n    return wait\n"
    root = repo(tmp_path, {"packages/p/src/p/x.py": source})
    renames = table(tmp_path, row("packages/p/src/p/x.py", "fn", "name", "wait", "class"))
    try:
        rename.run(root, renames)
    except rename.RenameError as exc:
        assert "would not parse" in str(exc)
    else:
        raise AssertionError("the run did not refuse")
    assert (root / "packages/p/src/p/x.py").read_text() == source


def test_a_lazy_reexport_table_resolves_and_tests_are_read(tmp_path):
    root = repo(tmp_path, {
        "packages/p/src/p/__init__.py": '''
_LAZY = {"connect": "_client", **dict.fromkeys(("Client",), "_client")}
def __getattr__(name):
    import importlib
    return getattr(importlib.import_module(f"{__name__}.{_LAZY[name]}"), name)
''',
        "packages/p/src/p/_client.py": "def connect(address, timeout=1):\n    return address\n",
        "packages/q/src/q/use.py": "import p\np.connect('a', timeout=2)\n",
        "packages/q/tests/test_use.py": "from p import connect\nconnect('b', timeout=3)\n",
    })
    renames = table(tmp_path, row("packages/p/src/p/_client.py", "connect", "name", "timeout",
                                  "timeout_s"))
    assert rename.run(root, renames) == 0
    assert "p.connect('a', timeout_s=2)" in (root / "packages/q/src/q/use.py").read_text()
    assert "connect('b', timeout_s=3)" in (root / "packages/q/tests/test_use.py").read_text()


def test_use_rows_on_one_line_apply_together_and_keep_covers_a_later_keyword_line(tmp_path,
                                                                                  capsys):
    root = repo(tmp_path, {
        "packages/p/src/p/x.py": "def fn(wait=1, timeout=1):\n    return wait\n",
        "packages/q/src/q/use.py": '''
fn = load()
fn(wait=1, timeout=2) or other.wait
proc.join(1,
          timeout=3)
''',
    })
    line = "fn(wait=1, timeout=2) or other.wait"
    renames = table(tmp_path,
                    row("packages/p/src/p/x.py", "fn", "name", "wait", "wait_s"),
                    row("packages/p/src/p/x.py", "fn", "name", "timeout", "timeout_s"),
                    row("packages/q/src/q/use.py", line, "use", "wait=", "wait_s="),
                    row("packages/q/src/q/use.py", line, "use", "timeout=", "timeout_s="),
                    row("packages/q/src/q/use.py", "timeout=3)", "keep", "timeout", "timeout"))
    assert rename.run(root, renames) == 0
    text = (root / "packages/q/src/q/use.py").read_text()
    assert "fn(wait_s=1, timeout_s=2) or other.wait" in text and "timeout=3)" in text
    assert rename.run(root, renames, dry_run=True) == 0
    assert "0 to apply, 5 applied, 0 not here" in capsys.readouterr().out


def test_a_keep_row_on_a_line_a_use_row_changes_stays_applied(tmp_path, capsys):
    root = repo(tmp_path, {
        "packages/p/src/p/x.py": "def fn(type=1):\n    return type\n",
        "packages/q/src/q/use.py": 'parse.add("--wait", type=float)\n',
    })
    line = 'parse.add("--wait", type=float)'
    renames = table(tmp_path,
                    row("packages/p/src/p/x.py", "fn", "name", "type", "kind"),
                    row("packages/q/src/q/use.py", line, "keep", "type", "type"),
                    row("packages/q/src/q/use.py", line, "use", '"--wait"', '"--wait-s"'))
    assert rename.run(root, renames) == 0
    assert (root / "packages/q/src/q/use.py").read_text() == \
        'parse.add("--wait-s", type=float)\n'
    capsys.readouterr()
    assert rename.run(root, renames, dry_run=True) == 0
    assert "0 to apply, 3 applied, 0 not here" in capsys.readouterr().out



REFUSED = {
    "new-name-already-local": ('''
def launch(req, environ=None):
    env = build(req)
    return send(env, environ)
''', ("launch", "environ", "env"), "'env' is already bound or read in launch"),
    "nested-lambda-and-def": ('''
def run(timeout=1):
    pick = lambda timeout: timeout * 2
    return pick(3) + timeout
''', ("run", "timeout", "timeout_s"), "a scope inside run has its own 'timeout'"),
    "module-name-and-function-local": ('''
HOME = "P_HOME"
def read(env):
    HOME = env.get("X")
    return HOME
''', ("-", "HOME", "HOME_ENV"), "'HOME' is also a local of a function there"),
}


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_a_row_that_would_change_what_a_token_means_is_refused(tmp_path, capsys, case):
    text, (scope, old, new), why = REFUSED[case]
    path = "packages/p/src/p/x.py"
    root = repo(tmp_path, {path: text})
    assert rename.run(root, table(tmp_path, row(path, scope, "name", old, new))) == 2
    out = capsys.readouterr().out
    assert f"row 2 ({path} {scope} {old} -> {new}): {path}:" in out and why in out
    assert (root / path).read_text() == text


def test_a_local_renamed_out_of_a_parameters_way_applies_with_it(tmp_path):
    path = "packages/p/src/p/x.py"
    root = repo(tmp_path, {path: '''
def launch(req, environ=None):
    """Sends `env`, built from `req`."""
    env = build(req)
    return send(env, environ)
'''})
    renames = table(tmp_path, row(path, "launch", "name", "environ", "env"),
                    row(path, "launch", "name", "env", "launch_env"))
    assert rename.run(root, renames) == 0
    text = (root / path).read_text()
    assert "def launch(req, env=None):" in text and "launch_env = build(req)" in text
    assert "send(launch_env, env)" in text and "`launch_env`" in text
    assert rename.run(root, renames, dry_run=True) == 0


def test_submodule_in_function_and_package_relative_imports_resolve(tmp_path):
    root = repo(tmp_path, {
        "packages/p/src/p/__init__.py": "from .impl import old\n",
        "packages/p/src/p/impl.py": "def old(value):\n    return value\n",
        "packages/q/src/q/use.py": '''
from p import impl
a = impl.old(1)
def late():
    from p.impl import old
    return old(2)
''',
    })
    renames = table(tmp_path, row("packages/p/src/p/impl.py", "-", "name", "old", "fresh"))
    assert rename.run(root, renames) == 0
    assert (root / "packages/p/src/p/__init__.py").read_text() == "from .impl import fresh\n"
    use = (root / "packages/q/src/q/use.py").read_text()
    assert "a = impl.fresh(1)" in use
    assert "from p.impl import fresh\n    return fresh(2)" in use


def test_a_parameter_row_leaves_self_attributes_and_reads_its_state_from_the_signature(
        tmp_path, capsys):
    path = "packages/p/src/p/x.py"
    root = repo(tmp_path, {path: '''
class Waiter:
    def __init__(self, timeout):
        self.timeout = timeout
    def left(self):
        return self.timeout
'''})
    renames = table(tmp_path, row(path, "Waiter.__init__", "name", "timeout", "timeout_s"))
    assert rename.run(root, renames) == 0
    text = (root / path).read_text()
    assert "def __init__(self, timeout_s):" in text
    assert "self.timeout = timeout_s" in text and "return self.timeout\n" in text
    capsys.readouterr()
    # The attribute still stores the old name; the row is applied all the same.
    assert rename.run(root, renames, dry_run=True) == 0
    assert "0 to apply, 1 applied, 0 not here" in capsys.readouterr().out


def test_a_parameter_cite_is_listed_only_in_its_own_file(tmp_path, capsys):
    root = repo(tmp_path, {
        "packages/p/src/p/x.py": '''
"""`run` waits `timeout` seconds."""
def run(timeout=1):
    """Give up after `timeout` seconds."""
''',
        "packages/q/src/q/y.py": '''
# `timeout` here is wait's own.
def wait(timeout=2):
    return timeout
''',
    })
    renames = table(tmp_path, row("packages/p/src/p/x.py", "run", "name", "timeout", "timeout_s"))
    assert rename.run(root, renames) == 1
    out = capsys.readouterr().out
    assert "x.py:2: comment still cites 'timeout'" in out
    assert "y.py" not in out and "1 leftovers" in out
    assert "`timeout` here is wait's own" in (root / "packages/q/src/q/y.py").read_text()


def test_a_use_row_changes_every_match_on_its_line_and_stops_at_a_word_end(tmp_path,
                                                                            capsys):
    path = "packages/q/src/q/use.py"
    line = "both = clock.wait + clock.wait * clock.wait_more + wait"
    root = repo(tmp_path, {path: f"{line}\n"})
    renames = table(tmp_path, row(path, line, "use", "clock.wait", "clock.wait_s"))
    assert rename.run(root, renames) == 0
    assert (root / path).read_text() == \
        "both = clock.wait_s + clock.wait_s * clock.wait_more + wait\n"
    capsys.readouterr()
    assert rename.run(root, renames, dry_run=True) == 0
    assert "0 to apply, 1 applied, 0 not here" in capsys.readouterr().out


def test_a_kwonly_call_with_a_star_at_or_before_its_position_is_a_leftover(tmp_path, capsys):
    root = repo(tmp_path, {
        "packages/p/src/p/git.py": "def changes(root, staged=True, rng=None):\n    return root\n",
        "packages/p/src/p/use.py": '''
from p.git import changes
rest = [False]
changes('.', *rest)
changes(*rest)
changes('.', False)
''',
    })
    renames = table(tmp_path,
                    row("packages/p/src/p/git.py", "changes", "kwonly", "staged", "staged"))
    assert rename.run(root, renames) == 1
    out = capsys.readouterr().out
    message = "changes call passes a starred argument that may reach keyword-only 'staged'"
    assert f"use.py:4: {message}" in out and f"use.py:5: {message}" in out
    assert "2 leftovers" in out
    use = (root / "packages/p/src/p/use.py").read_text()
    assert "changes('.', *rest)\nchanges(*rest)\n" in use
    assert "changes('.', staged=False)" in use


def test_a_row_that_applies_only_after_another_has_applies_in_a_later_pass(tmp_path, capsys):
    path = "packages/p/tests/test_page.py"
    root = repo(tmp_path, {path: '''
import pytest
@pytest.fixture
def folder(tmp_path):
    return tmp_path
@pytest.fixture
def page(folder):
    return folder
def outer(items):
    try:
        return items[0]
    except IndexError as e:
        def inner(e):
            return e
        return inner(e)
'''})
    renames = table(tmp_path,
                    row(path, "-", "name", "folder", "work_dir_fixture"),
                    row(path, "outer", "name", "e", "exc"),
                    row(path, "outer.inner", "name", "e", "err"),
                    row(path, "page", "name", "folder", "work_dir"))
    assert rename.run(root, renames) == 0
    text = (root / path).read_text()
    assert "def work_dir_fixture(tmp_path):" in text
    assert "def page(work_dir):\n    return work_dir\n" in text
    assert "except IndexError as exc:" in text and "return inner(exc)" in text
    assert "def inner(err):\n            return err\n" in text
    capsys.readouterr()
    assert rename.run(root, renames, dry_run=True) == 0
    assert "0 to apply, 4 applied, 0 not here" in capsys.readouterr().out


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
def test_a_chain_in_one_scope_applies_in_either_table_order_and_reads_applied(
        tmp_path, capsys, order):
    path = "packages/p/tests/test_stop.py"
    root = repo(tmp_path, {path: '''
def test_stops():
    stopped = run()
    record = []
    record.append(stopped)
    assert record == [stopped]
'''})
    chain = [row(path, "test_stops", "name", "stopped", "acted"),
             row(path, "test_stops", "name", "record", "stopped")]
    renames = table(tmp_path, *(chain[index] for index in order))
    assert rename.run(root, renames) == 0
    assert (root / path).read_text() == '''
def test_stops():
    acted = run()
    stopped = []
    stopped.append(acted)
    assert stopped == [acted]
'''
    capsys.readouterr()
    assert rename.run(root, renames) == 0
    assert "0 to apply, 2 applied, 0 not here" in capsys.readouterr().out
    assert "acted = run()\n    stopped = []" in (root / path).read_text()


def test_a_nested_scope_that_binds_the_new_name_itself_is_no_collision(tmp_path):
    path = "packages/p/src/p/doc.py"
    texts = {
        "before": '''
def types(text):
    def bad(at, why):
        return f"{at}: {why}"
    for at, char in enumerate(text):
        if char == "!":
            return bad(at, "bang")
''',
        # The nested function's row has applied already, the outer one's not yet.
        "half": '''
def types(text):
    def bad(position, why):
        return f"{position}: {why}"
    for at, char in enumerate(text):
        if char == "!":
            return bad(at, "bang")
''',
    }
    for name, text in texts.items():
        (tmp_path / name).mkdir()
        root = repo(tmp_path / name, {path: text})
        renames = table(tmp_path / name,
                        row(path, "types", "name", "at", "position"),
                        row(path, "types.bad", "name", "at", "position"))
        assert rename.run(root, renames) == 0, name
        out = (root / path).read_text()
        assert "for position, char in enumerate(text):" in out
        assert 'return bad(position, "bang")' in out
        assert 'def bad(position, why):\n        return f"{position}: {why}"' in out


def test_a_nested_scope_that_reads_the_outer_name_still_refuses(tmp_path, capsys):
    path = "packages/p/src/p/doc.py"
    root = repo(tmp_path, {path: '''
def types(text):
    at = 0
    def bad(position):
        return at + position
    return bad(1)
'''})
    renames = table(tmp_path, row(path, "types", "name", "at", "position"))
    assert rename.run(root, renames) == 2
    assert "'position' is already bound or read in types" in capsys.readouterr().out


def test_kwonly_rows_on_one_signature_write_one_star_and_name_every_later_argument(
        tmp_path):
    root = repo(tmp_path, {
        "packages/p/src/p/m.py": '''
class Choice:
    def __init__(self, slot, respawn=False, held=False, why="", chain=False):
        self.slot = slot
def f(a, b=False, c=False, *, d=None):
    return a
def resolve(d, cwd, logical, base=None):
    return d
''',
        "packages/p/src/p/use.py": '''
from p.m import Choice, f, resolve
Choice("x", True, False, "because", True)
f(1, True, False)
resolve("a", ".", True, "b")
''',
    })
    m = "packages/p/src/p/m.py"
    renames = table(tmp_path,
                    row(m, "Choice.__init__", "kwonly", "chain", "chain"),
                    row(m, "Choice.__init__", "kwonly", "held", "held"),
                    row(m, "Choice.__init__", "kwonly", "respawn", "respawn"),
                    row(m, "f", "kwonly", "b", "b"),
                    row(m, "f", "kwonly", "c", "c"),
                    row(m, "resolve", "kwonly", "logical", "logical"),
                    row(m, "resolve", "name", "base", "start"))
    assert rename.run(root, renames) == 0
    text = (root / m).read_text()
    assert 'def __init__(self, slot, *, respawn=False, held=False, why="", chain=False):' \
        in text
    assert "def f(a, *, b=False, c=False, d=None):" in text
    assert "def resolve(d, cwd, *, logical, start=None):" in text
    use = (root / "packages/p/src/p/use.py").read_text()
    assert 'Choice("x", respawn=True, held=False, why="because", chain=True)' in use
    assert "f(1, b=True, c=False)" in use
    assert 'resolve("a", ".", logical=True, start="b")' in use
    before = {path: (root / path).read_text() for path in (m, "packages/p/src/p/use.py")}
    assert rename.run(root, renames) == 0
    assert before == {path: (root / path).read_text() for path in before}


def test_edits_land_on_their_token_after_non_ascii_text_and_odd_line_breaks(tmp_path):
    path = "packages/p/src/p/x.py"
    root = repo(tmp_path, {path: (
        'SPACES = "  　"\n'
        'PAGE = "a\x0cb"\n'
        "def show(e):\n"
        '    print(f"→ {e.code}", e)\n'
        "    return e\n")})
    renames = table(tmp_path, row(path, "show", "name", "e", "exc"))
    assert rename.run(root, renames) == 0
    assert (root / path).read_text() == (
        'SPACES = "  　"\n'
        'PAGE = "a\x0cb"\n'
        "def show(exc):\n"
        '    print(f"→ {exc.code}", exc)\n'
        "    return exc\n")


def test_a_class_row_leaves_a_methods_own_bare_names_alone(tmp_path):
    path = "packages/p/src/p/x.py"
    root = repo(tmp_path, {path: '''
class Clock:
    age_seconds: float = 0.0
    def with_age(self, age_seconds):
        return Clock(age_seconds=age_seconds)
    def any(self):
        return any(self.age_seconds for _ in ())
'''})
    renames = table(tmp_path, row(path, "Clock", "name", "age_seconds", "age_s"),
                    row(path, "Clock", "name", "any", "has_age"),
                    row(path, "return any(self.age_s for _ in ())", "keep", "any", "any"))
    assert rename.run(root, renames) == 0
    assert (root / path).read_text() == '''
class Clock:
    age_s: float = 0.0
    def with_age(self, age_seconds):
        return Clock(age_s=age_seconds)
    def has_age(self):
        return any(self.age_s for _ in ())
'''
