"""The render audit hook, `moxi.render`. An audit hook cannot be removed, so every test that
installs one runs in a child process."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

from moxi import render

#: A small segment package with a module of its own to import, so an import under the
#: hook has bytecode to try to write.
SEGMENT_PACKAGE = {
    "__init__.py": "",
    "words.py": "PREFIX = 'seg:'\n",
    "segment.py": textwrap.dedent('''
        from .words import PREFIX


        class Shown:
            def __init__(self, text):
                self.text = text

            def coloured(self):
                return self.text


        def segment(session_id, now=None):
            return Shown(PREFIX + session_id)
    '''),
}


def child(code: str, *args, cwd=None, env=None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code), *args],
                          capture_output=True, text=True, timeout=60, cwd=cwd,
                          env=dict(os.environ) if env is None else env)


def byte_code_on(tmp_path) -> dict[str, str]:
    """The test's environment with byte code writes left on, each to a prefix under
    `tmp_path`, so an import tries one and nothing lands beside a source."""
    return {**os.environ, "PYTHONDONTWRITEBYTECODE": "",
            "PYTHONPYCACHEPREFIX": str(tmp_path / "pycache")}


def segment_package(root, name="fakeseg"):
    package = root / name
    package.mkdir(parents=True)
    for file, text in SEGMENT_PACKAGE.items():
        (package / file).write_text(text)
    return package


def test_refused_is_a_permission_error():
    assert issubclass(render.Refused, PermissionError)
    assert issubclass(render.Refused, OSError)


def test_the_hook_refuses_effects_and_passes_reads(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "r.txt").write_text("read me")
    probe = r'''
        import os, socket, sqlite3, subprocess, sys, pathlib, tempfile
        from moxi import render
        render.install()
        assert sys.dont_write_bytecode
        d = pathlib.Path(sys.argv[1])

        def t(name, f):
            try:
                f()
                print(name, "allowed")
            except render.Refused:
                print(name, "refused")

        t("read", lambda: open(d / "r.txt").read())
        t("read bytes", lambda: (d / "r.txt").read_bytes())
        t("os.open read", lambda: os.close(os.open(d / "r.txt", os.O_RDONLY)))
        t("stat", lambda: os.stat(d / "r.txt"))
        t("listdir", lambda: os.listdir(d))
        t("open w", lambda: open(d / "w.txt", "w"))
        t("open a", lambda: open(d / "r.txt", "a"))
        t("open r+", lambda: open(d / "r.txt", "r+"))
        t("os.open creat", lambda: os.open(d / "c.txt", os.O_CREAT | os.O_WRONLY))
        t("touch", lambda: (d / "t.txt").touch())
        t("replace", lambda: os.replace(d / "r.txt", d / "r2.txt"))
        t("unlink", lambda: os.unlink(d / "r.txt"))
        t("mkdir", lambda: os.mkdir(d / "sub"))
        t("chmod", lambda: os.chmod(d / "r.txt", 0o600))
        t("utime", lambda: os.utime(d / "r.txt"))
        t("symlink", lambda: os.symlink(d / "r.txt", d / "l"))
        t("mkstemp", lambda: tempfile.mkstemp(dir=d))
        t("subprocess", lambda: subprocess.run(["true"]))
        t("system", lambda: os.system("true"))
        t("kill", lambda: os.kill(os.getpid(), 0))
        if hasattr(socket, "AF_UNIX"):
            family, there, here = socket.AF_UNIX, str(d / "sock"), str(d / "sock")
        else:  # Windows' Python has no Unix sockets: a TCP one there
            family, there, here = socket.AF_INET, ("127.0.0.1", 9), ("127.0.0.1", 0)
        t("connect", lambda: socket.socket(family).connect(there))
        t("bind", lambda: socket.socket(family).bind(here))
        t("sqlite rw", lambda: sqlite3.connect(str(d / "x.db")))
        t("sqlite memory", lambda: sqlite3.connect(":memory:"))
        t("sqlite uri rw", lambda: sqlite3.connect(f"file:{d}/x.db?mode=rwc", uri=True))
        t("sqlite ro", lambda: sqlite3.connect(f"file:{d}/r.txt?mode=ro", uri=True).close())
    '''
    proc = child(probe, str(work))
    assert proc.returncode == 0, proc.stderr
    said = dict(line.rsplit(" ", 1) for line in proc.stdout.splitlines())
    allowed = {"read", "read bytes", "os.open read", "stat", "listdir", "sqlite ro"}
    assert {k for k, v in said.items() if v == "allowed"} == allowed
    assert len(said) == 26
    assert sorted(p.name for p in work.iterdir()) == ["r.txt"]


def test_an_import_with_no_bytecode_goes_on_under_the_hook(tmp_path):
    """The hook alone, with bytecode writes left on: the import tries to write each
    module's bytecode, the hook refuses with a PermissionError, and the import goes on
    without it, as it does in a read-only directory."""
    package = segment_package(tmp_path / "pkg")
    probe = r'''
        import sys
        sys.path.insert(0, sys.argv[1])
        from moxi import render
        refused = []

        def hook(event, args):
            try:
                render._hook(event, args)
            except render.Refused:
                refused.append(event)
                raise
        sys.addaudithook(hook)
        assert not sys.dont_write_bytecode
        import fakeseg.segment as segment
        assert segment.__file__.startswith(sys.argv[1])
        print(segment.segment("abc").text, len(refused))
    '''
    proc = child(probe, str(tmp_path / "pkg"), env=byte_code_on(tmp_path))
    assert proc.returncode == 0, proc.stderr
    text, refusals = proc.stdout.split()
    assert text == "seg:abc" and int(refusals) >= 1
    assert not (package / "__pycache__").exists()


def test_a_hook_raising_another_error_breaks_that_import(tmp_path):
    """Why a refusal is a PermissionError: the import machinery skips a bytecode write
    that fails with an OSError and no other."""
    segment_package(tmp_path / "pkg")
    probe = r'''
        import sys
        sys.path.insert(0, sys.argv[1])
        from moxi import render

        def hook(event, args):
            try:
                render._hook(event, args)
            except render.Refused as e:
                raise RuntimeError(str(e)) from None
        sys.addaudithook(hook)
        try:
            import fakeseg.segment
        except RuntimeError:
            print("import failed")
    '''
    proc = child(probe, str(tmp_path / "pkg"), env=byte_code_on(tmp_path))
    assert proc.stdout.strip() == "import failed", proc.stderr


def test_load_segment_installs_the_hook_then_imports(tmp_path):
    package = segment_package(tmp_path / "pkg")
    proc = child('''
        import sys
        sys.path.insert(0, sys.argv[1])
        from moxi import render
        assert not render.installed()
        module = render.load_segment("fakeseg.segment")
        print(render.installed(), sys.dont_write_bytecode, module.segment("x").text)
    ''', str(tmp_path / "pkg"))
    assert proc.stdout.split() == ["True", "True", "seg:x"], proc.stderr
    assert not (package / "__pycache__").exists()


def test_load_segment_refuses_a_segment_module_that_writes(tmp_path):
    (tmp_path / "evil_segment.py").write_text(textwrap.dedent(f'''
        open({str(tmp_path / "written")!r}, "w").write("x")

        def segment(session_id, now=None):
            return None
    '''))
    proc = child('''
        import sys
        sys.path.insert(0, sys.argv[1])
        from moxi import render
        try:
            render.load_segment("evil_segment")
        except render.Refused as e:
            print("refused:", e)
    ''', str(tmp_path))
    assert proc.stdout.startswith("refused:") and "for writing" in proc.stdout, proc.stderr
    assert not (tmp_path / "written").exists()


def test_install_twice_adds_one_hook():
    proc = child('''
        import sys
        from moxi import render
        added = []
        real = sys.addaudithook
        sys.addaudithook = lambda hook: (added.append(hook), real(hook))
        render.install()
        render.install()
        print(len(added), render.installed())
    ''')
    assert proc.stdout.split() == ["1", "True"], proc.stderr
