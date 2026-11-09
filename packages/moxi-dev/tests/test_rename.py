import subprocess
from pathlib import Path

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

