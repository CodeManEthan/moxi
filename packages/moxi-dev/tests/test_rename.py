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
