"""moxi.files: atomic writes, locks, update, records and private directories."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from moxi import files

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
WINDOWS = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def mode_of(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


# -- atomic_write ------------------------------------------------------------------------

def test_atomic_write_writes_bytes_and_text(work):
    target = work / "a.txt"
    files.atomic_write(target, b"one")
    assert target.read_bytes() == b"one"
    files.atomic_write(target, "twó")
    assert target.read_bytes() == "twó".encode("utf-8")
    assert [p.name for p in work.iterdir()] == ["a.txt"]


@POSIX
def test_atomic_write_sets_the_mode(tmp_path):
    target = tmp_path / "a"
    files.atomic_write(target, "x")
    assert mode_of(target) == 0o600
    files.atomic_write(target, "x", mode=0o644)
    assert mode_of(target) == 0o644


def test_atomic_write_makes_a_missing_parent(tmp_path):
    target = tmp_path / "deep" / "er" / "a"
    files.atomic_write(target, "x")
    assert target.read_text() == "x"


def test_a_failed_atomic_write_leaves_the_file_and_no_temp(work, monkeypatch):
    target = work / "a"
    target.write_text("old")

    def broken(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(files, "_replace", broken)
    with pytest.raises(OSError, match="disk full"):
        files.atomic_write(target, "new")
    assert target.read_text() == "old"
    assert [p.name for p in work.iterdir()] == ["a"]


def test_atomic_write_uses_a_dotted_temp_name(tmp_path, monkeypatch):
    seen = []
    real = files._replace

    def spy(src, dst):
        seen.append(Path(src).name)
        real(src, dst)

    monkeypatch.setattr(files, "_replace", spy)
    files.atomic_write(tmp_path / "state.json", "{}")
    (name,) = seen
    assert name.startswith(".state.json.") and name.endswith(".tmp")
    assert len(name.removeprefix(".state.json.").removesuffix(".tmp")) == 8


@WINDOWS
def test_atomic_write_retries_a_sharing_violation(tmp_path, monkeypatch):
    target = tmp_path / "a"
    calls = []
    real = os.replace

    def flaky(src, dst):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("in use")
        real(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    files.atomic_write(target, "x")
    assert target.read_text() == "x" and len(calls) == 3


# -- lock --------------------------------------------------------------------------------

HOLDER = textwrap.dedent("""
    import sys, time
    from moxi import files
    with files.lock(sys.argv[1]):
        print("held", flush=True)
        time.sleep(float(sys.argv[2]))
""")


def hold(path: Path, seconds: float = 30.0) -> subprocess.Popen:
    """A real process that holds the lock on `path` until killed or `seconds` pass."""
    proc = subprocess.Popen([sys.executable, "-c", HOLDER, str(path), str(seconds)],
                            stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "held"
    return proc


@pytest.fixture
def holder(tmp_path):
    procs = []

    def start(path, seconds=30.0):
        proc = hold(path, seconds)
        procs.append(proc)
        return proc

    yield start
    for proc in procs:
        proc.kill()
        proc.wait()


def test_lock_creates_the_file_and_never_removes_it(tmp_path):
    path = tmp_path / "x.lock"
    with files.lock(path):
        assert path.read_text().strip() == str(os.getpid())
    assert path.exists()
    if sys.platform != "win32":
        assert mode_of(path) == 0o600


def test_timeout_zero_raises_at_once_naming_the_holder(tmp_path, holder):
    path = tmp_path / "x.lock"
    proc = holder(path)
    started = time.monotonic()
    with pytest.raises(files.LockTimeout) as caught:
        with files.lock(path, timeout=0):
            pass
    assert time.monotonic() - started < 0.5
    assert caught.value.holder == proc.pid
    assert caught.value.path == path
    assert caught.value.exit_code == 75
    assert str(proc.pid) in str(caught.value)


def test_a_float_timeout_waits_that_long(tmp_path, holder):
    path = tmp_path / "x.lock"
    holder(path)
    started = time.monotonic()
    with pytest.raises(files.LockTimeout):
        with files.lock(path, timeout=0.3):
            pass
    assert 0.3 <= time.monotonic() - started < 2.0


def test_no_timeout_waits_for_the_holder(tmp_path, holder):
    path = tmp_path / "x.lock"
    holder(path, seconds=0.5)
    started = time.monotonic()
    with files.lock(path):
        assert path.read_text().strip() == str(os.getpid())
    assert time.monotonic() - started >= 0.3


def test_a_killed_holder_frees_the_lock_at_once(tmp_path, holder):
    path = tmp_path / "x.lock"
    proc = holder(path)
    proc.kill()
    proc.wait()
    with files.lock(path, timeout=0):
        pass


def test_the_lock_is_not_reentrant(tmp_path):
    path = tmp_path / "x.lock"
    with files.lock(path):
        with pytest.raises(RuntimeError, match="not re-entrant"):
            with files.lock(path):
                pass
        with pytest.raises(RuntimeError):
            with files.lock(tmp_path / "." / "x.lock", timeout=0):
                pass
    with files.lock(path, timeout=0):
        pass


def test_another_thread_of_this_process_is_refused(tmp_path):
    """The lock is the process's: a second thread taking a held path raises at once
    rather than waiting for its own process."""
    path = tmp_path / "x.lock"
    seen = []

    def other():
        try:
            with files.lock(path, timeout=5):
                seen.append("took it")
        except RuntimeError as exc:
            seen.append(str(exc))

    with files.lock(path):
        thread = threading.Thread(target=other)
        started = time.monotonic()
        thread.start()
        thread.join(10)
        waited = time.monotonic() - started
    assert len(seen) == 1 and "another thread of this process" in seen[0]
    assert waited < 2
    with files.lock(path, timeout=0):
        pass


def test_threads_updating_one_file_take_turns(tmp_path):
    """update, edit_toml and append_record queue this process's threads for the lock."""
    target = tmp_path / "n"
    log = tmp_path / "r.jsonl"
    toml = tmp_path / "c.toml"
    errors = []

    def bump(old):
        return str(int(old or b"0") + 1).encode()

    def work(w):
        try:
            for i in range(20):
                files.update(target, bump)
                files.append_record(log, {"w": w, "i": i}, max_bytes=200)
                with files.edit_toml(toml) as doc:
                    doc[f"w{w}"] = doc.get(f"w{w}", 0) + 1
        except Exception as exc:  # noqa: BLE001 - collected for the assertion
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(w,)) for w in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    assert errors == []
    assert target.read_text() == "120"
    assert len(files.read_records(log)) > 0


def test_update_inside_its_own_lock_is_refused_not_hung(tmp_path):
    target = tmp_path / "n"
    with files.lock(tmp_path / "n.lock"):
        with pytest.raises(RuntimeError, match="this thread"):
            files.update(target, lambda old: b"x")

    def nested(old):
        files.update(target, lambda inner: b"y")
        return b"x"

    with pytest.raises(RuntimeError, match="this thread"):
        files.update(target, nested)


def test_an_error_in_the_block_releases_the_lock(tmp_path):
    path = tmp_path / "x.lock"
    with pytest.raises(KeyError):
        with files.lock(path):
            raise KeyError("x")
    with files.lock(path, timeout=0):
        pass


def test_a_holder_with_no_pid_is_named_as_another_process(tmp_path):
    err = files.LockTimeout(tmp_path / "x.lock", None)
    assert err.holder is None and "another process" in str(err)


# -- the core lock: nested calls and the install's claim ---------------------------------

NO_PASS_FDS = pytest.mark.skipif(
    sys.platform == "win32",
    reason="hands the claim over with pass_fds; Windows hands a handle (run on Windows)")


@pytest.fixture
def core(tmp_path, monkeypatch):
    """The core lock's path under a tmp `MOXI_HOME`, with no claim adopted yet."""
    from moxi import paths

    monkeypatch.setenv("MOXI_HOME", str(tmp_path / "moxi"))
    monkeypatch.delenv(files.CLAIM_VARIABLE, raising=False)
    monkeypatch.setattr(files, "_claim", None)
    monkeypatch.setattr(files, "_adopted", False)
    return paths.core_lock()


def named(fd: int) -> str:
    """The variable's text for descriptor `fd`: the descriptor, or on Windows its handle."""
    if sys.platform == "win32":
        import msvcrt

        return str(msvcrt.get_osfhandle(fd))
    return str(fd)


def taken_elsewhere(path: Path) -> bool:
    """Whether another process finds `path` locked."""
    code = ("import sys\nfrom moxi import files\n"
            "try:\n    with files.lock(sys.argv[1], timeout=0): pass\n"
            "except files.LockTimeout: sys.exit(3)\n")
    result = subprocess.run([sys.executable, "-c", code, str(path)], timeout=30)
    return result.returncode == 3


def test_nested_on_any_other_path_raises_before_taking_anything(core, tmp_path):
    other = tmp_path / "other.lock"
    with pytest.raises(ValueError, match="core lock alone"):
        files.lock(other, nested=True)
    assert not other.exists()
    with pytest.raises(ValueError):
        files.lock(core.parent / "install.lock.x", nested=True)


def test_nested_compares_the_path_made_absolute(core, monkeypatch):
    core.parent.mkdir(parents=True)
    monkeypatch.chdir(core.parent.parent)
    with files.lock(Path(core.parent.name) / ".." / core.parent.name / core.name,
                    nested=True, timeout=0):
        assert core.read_text().strip() == str(os.getpid())


def test_a_standalone_nested_call_takes_the_lock(core):
    with files.lock(core, nested=True, timeout=0):
        assert core.read_text().strip() == str(os.getpid())
        assert taken_elsewhere(core)
    assert not taken_elsewhere(core)


def test_a_standalone_nested_call_waits_for_an_install(core, holder):
    proc = holder(core)
    started = time.monotonic()
    with pytest.raises(files.LockTimeout) as caught:
        with files.lock(core, nested=True, timeout=0.3):
            pass
    assert time.monotonic() - started >= 0.3
    assert caught.value.holder == proc.pid
    proc.kill()
    proc.wait()
    with files.lock(core, nested=True):
        pass


def test_under_the_installs_own_hold_nested_yields_on_every_thread(core):
    """The install's process holds the lock without `nested`: a nested call from that
    thread or any other neither waits nor raises, and takes nothing."""
    seen = []

    def other():
        try:
            with files.lock(core, nested=True, timeout=0):
                seen.append("yielded")
        except Exception as exc:  # noqa: BLE001 - collected for the assertion
            seen.append(repr(exc))

    with files.lock(core):
        with files.lock(core, nested=True, timeout=0):
            with files.lock(core, nested=True, timeout=0):
                pass
        thread = threading.Thread(target=other)
        thread.start()
        thread.join(10)
        assert seen == ["yielded"]
        assert taken_elsewhere(core)
    assert not taken_elsewhere(core)


def test_a_nested_call_inside_a_standalone_nested_hold_yields_on_its_thread(core):
    """A standalone caller holds the lock around several core calls that take it too."""
    with files.lock(core, nested=True, timeout=0):
        with files.lock(core, nested=True, timeout=0):
            pass
        assert taken_elsewhere(core)
    assert not taken_elsewhere(core)


def test_another_thread_of_a_standalone_caller_waits_its_turn(core):
    """A standalone caller's hold covers its own thread only: a second thread's nested
    call waits for it (never sharing the span, never raising), within its timeout."""
    events = []
    inside = threading.Event()
    release = threading.Event()

    def first():
        with files.lock(core, nested=True):
            events.append("first in")
            inside.set()
            release.wait(10)
            events.append("first out")

    def second(timeout):
        try:
            with files.lock(core, nested=True, timeout=timeout):
                events.append("second in")
        except files.LockTimeout:
            events.append("second timed out")

    one = threading.Thread(target=first)
    one.start()
    inside.wait(10)
    started = time.monotonic()
    second(0.2)
    assert time.monotonic() - started >= 0.2
    two = threading.Thread(target=second, args=(None,))
    two.start()
    time.sleep(0.3)
    assert events == ["first in", "second timed out"]
    release.set()
    one.join(10)
    two.join(10)
    assert events == ["first in", "second timed out", "first out", "second in"]


def test_a_thread_still_waiting_to_take_the_lock_covers_no_other(core, holder):
    """While one thread of a standalone caller waits for another process's hold, a
    second thread's nested call waits too; it never yields on a lock nobody here holds."""
    proc = holder(core, seconds=1.0)
    events = []

    def first():
        with files.lock(core, nested=True):
            events.append("first in")

    one = threading.Thread(target=first)
    one.start()
    time.sleep(0.2)
    with pytest.raises(files.LockTimeout):
        with files.lock(core, nested=True, timeout=0.2):
            events.append("second in")
    one.join(10)
    proc.wait()
    assert events == ["first in"]


def test_a_plain_take_still_waiting_covers_no_nested_call(core, holder):
    """While one thread waits on a plain take of the core lock, another process holding
    it, a second thread's nested call never yields: a take not yet made covers nothing.
    The nested call then meets the plain take as any second take does, and is refused."""
    proc = holder(core, seconds=1.0)
    events = []

    def first():
        with files.lock(core):
            events.append("first in")

    one = threading.Thread(target=first)
    one.start()
    time.sleep(0.2)
    with pytest.raises(RuntimeError, match="another thread of this process"):
        with files.lock(core, nested=True, timeout=0.2):
            events.append("second in")
    one.join(10)
    proc.wait()
    assert events == ["first in"]


def test_the_install_hold_refuses_a_second_plain_take(core):
    with files.lock(core, nested=True):
        with pytest.raises(RuntimeError, match="not re-entrant"):
            with files.lock(core):
                pass


def test_descriptor_names_the_held_lock_file(core, tmp_path):
    with pytest.raises(RuntimeError):
        files._descriptor(core)
    with files.lock(core):
        fd = files._descriptor(core)
        assert files._same_file(fd, core)
    with pytest.raises(RuntimeError):
        files._descriptor(core)


CHILD = textwrap.dedent("""
    import json, os, subprocess, sys, threading, time
    from moxi import files, paths

    GRANDCHILD = '''
    import json, os, sys, time
    from moxi import files, paths
    path = paths.core_lock()
    out = {"variable": os.environ.get(files.CLAIM_VARIABLE),
           "descriptor": files._same_file(int(sys.argv[1]), path),
           "adopted": files.adopt_core_claim(path)}
    started = time.monotonic()
    try:
        with files.lock(path, nested=True, timeout=0.3):
            out["took"] = True
    except files.LockTimeout as exc:
        out["waited"] = time.monotonic() - started
        out["holder"] = exc.holder
    print(json.dumps(out))
    '''

    path = paths.core_lock()
    named = os.environ[files.CLAIM_VARIABLE]
    out = {"adopted": files.adopt_core_claim(path),
           "variable": os.environ.get(files.CLAIM_VARIABLE)}
    out["again"] = files.adopt_core_claim(path)
    out["inheritable"] = os.get_inheritable(int(named))
    started = time.monotonic()
    with files.lock(path, nested=True, timeout=0):
        with files.lock(path, nested=True, timeout=0):
            pass
    seen = []
    def other():
        with files.lock(path, nested=True, timeout=0):
            seen.append(True)
    thread = threading.Thread(target=other)
    thread.start()
    thread.join(10)
    out["thread"] = seen == [True]
    out["seconds"] = time.monotonic() - started
    grand = subprocess.run([sys.executable, "-c", GRANDCHILD, named],
                           capture_output=True, text=True, timeout=60)
    out["grandchild"] = json.loads(grand.stdout or "null") or grand.stderr
    print(json.dumps(out))
""")


def start_child(core, script, value):
    """A child of this process, started as the install starts its settle child: the
    lock's descriptor passed to it alone and named in its environment alone."""
    fd = files._descriptor(core)
    env = {**os.environ, files.CLAIM_VARIABLE: value(fd)}
    return subprocess.run([sys.executable, "-c", script], env=env, pass_fds=(fd,),
                          capture_output=True, text=True, timeout=60)


@NO_PASS_FDS
def test_a_child_that_adopts_the_claim_works_under_its_parents_lock(core):
    with files.lock(core):
        child = start_child(core, CHILD, str)
        assert child.returncode == 0, child.stderr
        out = json.loads(child.stdout)
        assert files.CLAIM_VARIABLE not in os.environ
        assert taken_elsewhere(core)
    assert out["adopted"] is True and out["again"] is True
    assert out["variable"] is None
    assert out["inheritable"] is False
    assert out["thread"] is True
    assert out["seconds"] < 2
    grand = out["grandchild"]
    assert isinstance(grand, dict), grand
    assert grand["variable"] is None
    assert grand["descriptor"] is False
    assert grand["adopted"] is False
    assert "took" not in grand
    assert grand["waited"] >= 0.3 and grand["holder"] == os.getpid()


FORGED = textwrap.dedent("""
    import json, os, sys
    from moxi import files, paths
    path = paths.core_lock()
    out = {"adopted": files.adopt_core_claim(path),
           "variable": os.environ.get(files.CLAIM_VARIABLE)}
    try:
        with files.lock(path, nested=True, timeout=0.3):
            out["took"] = True
    except files.LockTimeout as exc:
        out["holder"] = exc.holder
    print(json.dumps(out))
""")


@NO_PASS_FDS
@pytest.mark.parametrize("value", [
    pytest.param(lambda fd: "999", id="a closed descriptor"),
    pytest.param(lambda fd: "0", id="a descriptor open on another file"),
    pytest.param(lambda fd: "seven", id="not a number"),
    pytest.param(lambda fd: "-1", id="a negative number"),
    pytest.param(lambda fd: "\u00b2", id="a superscript digit"),
    pytest.param(lambda fd: "\u0663", id="a digit that is not ASCII"),
])
def test_a_forged_claim_is_never_honoured(core, value):
    with files.lock(core):
        child = start_child(core, FORGED, value)
    assert child.returncode == 0, child.stderr
    out = json.loads(child.stdout)
    assert out == {"adopted": False, "variable": None, "holder": os.getpid()}


def test_a_child_with_no_claim_variable_holds_no_claim(core):
    assert files.adopt_core_claim(core) is False
    with files.lock(core, nested=True, timeout=0):
        assert core.read_text().strip() == str(os.getpid())


def test_lock_never_reads_the_variable(core, holder, monkeypatch):
    """A standalone process whose environment names a descriptor open on the lock file
    still takes the lock: only adopt_core_claim reads the variable."""
    proc = holder(core)
    fd = os.open(core, os.O_RDONLY)
    try:
        monkeypatch.setenv(files.CLAIM_VARIABLE, str(fd))
        with pytest.raises(files.LockTimeout) as caught:
            with files.lock(core, nested=True, timeout=0.2):
                pass
        assert caught.value.holder == proc.pid
        assert os.environ[files.CLAIM_VARIABLE] == str(fd)
    finally:
        os.close(fd)


def test_adoption_deletes_the_variable_and_a_second_call_reads_nothing(core, tmp_path,
                                                                        monkeypatch):
    core.parent.mkdir(parents=True)
    core.touch()
    fd = os.open(core, os.O_RDONLY)
    try:
        os.set_inheritable(fd, True)
        monkeypatch.setenv(files.CLAIM_VARIABLE, named(fd))
        assert files.adopt_core_claim(core) is True
        assert files.CLAIM_VARIABLE not in os.environ
        if sys.platform != "win32":
            assert os.get_inheritable(fd) is False
        monkeypatch.setenv(files.CLAIM_VARIABLE, "999")
        assert files.adopt_core_claim(core) is True
        assert os.environ[files.CLAIM_VARIABLE] == "999"
    finally:
        os.close(fd)


def test_a_failed_adoption_stays_failed(core, monkeypatch):
    monkeypatch.setenv(files.CLAIM_VARIABLE, "999")
    assert files.adopt_core_claim(core) is False
    assert files.CLAIM_VARIABLE not in os.environ
    assert files.adopt_core_claim(core) is False


def test_an_adopted_claim_lets_nested_calls_yield(core, holder):
    """In process: a claim open on the lock file yields while another process holds it."""
    proc = holder(core)
    fd = os.open(core, os.O_RDONLY)
    try:
        os.environ[files.CLAIM_VARIABLE] = named(fd)
        assert files.adopt_core_claim(core) is True
        with files.lock(core, nested=True, timeout=0):
            pass
        assert core.read_text().strip() == str(proc.pid)
    finally:
        os.close(fd)


@NO_PASS_FDS
def test_a_claim_that_no_longer_checks_is_dropped_with_a_warning(core, tmp_path, capfd):
    """Code in the child that closes the claim's descriptor, or opens another file on its
    number, leaves no claim: the lock is taken as a standalone caller takes it."""
    core.parent.mkdir(parents=True)
    core.touch()
    fd = os.open(core, os.O_RDONLY)
    os.environ[files.CLAIM_VARIABLE] = str(fd)
    assert files.adopt_core_claim(core) is True
    other = os.open(tmp_path / "elsewhere", os.O_RDWR | os.O_CREAT, 0o600)
    os.dup2(other, fd, inheritable=False)
    os.close(other)
    try:
        with files.lock(core, nested=True, timeout=0):
            assert core.read_text().strip() == str(os.getpid())
            assert taken_elsewhere(core)
        err = capfd.readouterr().err
        assert err.count("\n") == 1 and "lock claim" in err
        assert files.adopt_core_claim(core) is False
        with files.lock(core, nested=True, timeout=0):
            pass
        assert capfd.readouterr().err == ""
    finally:
        os.close(fd)


# -- update ------------------------------------------------------------------------------

def test_update_reads_changes_and_writes(tmp_path):
    target = tmp_path / "n"
    seen = []

    def bump(old):
        seen.append(old)
        return str(int(old or b"0") + 1).encode()

    assert files.update(target, bump) == b"1"
    assert files.update(target, bump) == b"2"
    assert seen == [None, b"1"]
    assert target.read_bytes() == b"2"
    assert (tmp_path / "n.lock").exists()


def test_update_writes_nothing_when_fn_raises(tmp_path):
    target = tmp_path / "n"
    target.write_bytes(b"old")

    def boom(old):
        raise ValueError("no")

    with pytest.raises(ValueError):
        files.update(target, boom)
    assert target.read_bytes() == b"old"


COUNTER = textwrap.dedent("""
    import sys
    from moxi import files
    for _ in range(int(sys.argv[2])):
        files.update(sys.argv[1], lambda old: str(int(old or b"0") + 1).encode())
""")


def test_update_from_many_processes_loses_nothing(tmp_path):
    target = tmp_path / "n"
    procs = [subprocess.Popen([sys.executable, "-c", COUNTER, str(target), "25"])
             for _ in range(4)]
    for proc in procs:
        assert proc.wait(60) == 0
    assert target.read_bytes() == b"100"


# -- edit_toml ---------------------------------------------------------------------------

EDITED = textwrap.dedent("""\
    # the module's settings
    backend = "local"   # where messages go

    [local]
    # kept for a month
    retain_days = 30
    record = ""
""")


def test_edit_toml_keeps_comments_and_order(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text(EDITED)
    with files.edit_toml(target) as doc:
        doc["local"]["retain_days"] = 7
        doc["block"] = True
    assert target.read_text() == (
        EDITED.replace("retain_days = 30", "retain_days = 7")
        .replace("# where messages go\n", "# where messages go\nblock = true\n"))


def test_edit_toml_writes_nothing_when_the_block_raises(work):
    target = work / "config.toml"
    target.write_text(EDITED)
    with pytest.raises(KeyError):
        with files.edit_toml(target) as doc:
            doc["backend"] = "remote"
            raise KeyError("no")
    assert target.read_text() == EDITED
    assert sorted(p.name for p in work.iterdir()) == ["config.toml", "config.toml.lock"]


def test_edit_toml_of_a_missing_file_starts_empty_and_writes_0600(tmp_path):
    target = tmp_path / "new" / "config.toml"
    with files.edit_toml(target) as doc:
        assert doc.unwrap() == {}
        doc["a"] = 1
    assert target.read_text() == "a = 1\n"
    if sys.platform != "win32":
        assert mode_of(target) == 0o600


@POSIX
def test_edit_toml_keeps_an_existing_files_mode(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("a = 1\n")
    os.chmod(target, 0o644)
    with files.edit_toml(target) as doc:
        doc["a"] = 2
    assert target.read_text() == "a = 2\n"
    assert mode_of(target) == 0o644


@POSIX
def test_edit_toml_writes_through_a_symbolic_link(tmp_path):
    real = tmp_path / "dotfiles" / "config.toml"
    real.parent.mkdir()
    real.write_text("a = 1\n")
    link = tmp_path / "config.toml"
    link.symlink_to(real)
    with files.edit_toml(link) as doc:
        doc["a"] = 2
    assert link.is_symlink()
    assert real.read_text() == "a = 2\n"


def test_edit_toml_refuses_a_file_that_is_not_toml(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("a = \n")
    with pytest.raises(ValueError):
        with files.edit_toml(target):
            pytest.fail("the block ran")
    assert target.read_text() == "a = \n"


def test_edit_toml_holds_the_files_lock(tmp_path, holder):
    target = tmp_path / "config.toml"
    holder(tmp_path / "config.toml.lock")
    with pytest.raises(files.LockTimeout):
        with files.edit_toml(target, timeout=0.1):
            pytest.fail("the block ran")
    assert not target.exists()


def test_importing_moxi_files_loads_no_tomlkit():
    code = ("import sys, moxi.files\n"
            "print(sorted(m for m in sys.modules if 'tomlkit' in m))\n")
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                          text=True, check=True)
    assert done.stdout.strip() == "[]"


# -- append_record and read_records ------------------------------------------------------

def test_append_record_writes_one_compact_line(tmp_path):
    log = tmp_path / "runs.jsonl"
    files.append_record(log, {"a": 1, "b": "é"})
    files.append_record(log, {"a": 2})
    assert log.read_bytes() == '{"a":1,"b":"é"}\n{"a":2}\n'.encode()
    if sys.platform != "win32":
        assert mode_of(log) == 0o600
    assert files.read_records(log) == [{"a": 1, "b": "é"}, {"a": 2}]


def test_append_record_rolls_to_one_older_generation(work):
    log = work / "r.jsonl"
    line = len(json.dumps({"i": 0}, separators=(",", ":"))) + 1
    for i in range(10):
        files.append_record(log, {"i": i}, max_bytes=line * 4)
    assert [r["i"] for r in files.read_records(log)] == [4, 5, 6, 7, 8, 9]
    assert [json.loads(x)["i"] for x in (work / "r.jsonl.1").read_text().splitlines()] \
        == [4, 5, 6, 7]
    assert [json.loads(x)["i"] for x in log.read_text().splitlines()] == [8, 9]
    assert sorted(p.name for p in work.iterdir()) == ["r.jsonl", "r.jsonl.1",
                                                          "r.jsonl.lock"]


def test_the_roll_goes_through_the_retrying_replace(tmp_path, monkeypatch):
    """The roll moves the log the way atomic_write replaces a file, so on Windows it
    retries a sharing violation rather than losing the record."""
    log = tmp_path / "r.jsonl"
    moves = []
    real = files._replace

    def spy(src, dst):
        moves.append((Path(src).name, Path(dst).name))
        real(src, dst)

    monkeypatch.setattr(files, "_replace", spy)
    for i in range(3):
        files.append_record(log, {"i": i}, max_bytes=20)
    assert ("r.jsonl", "r.jsonl.1") in moves


@WINDOWS
def test_the_roll_retries_a_sharing_violation(tmp_path, monkeypatch):
    log = tmp_path / "r.jsonl"
    files.append_record(log, {"i": 0}, max_bytes=20)
    calls = []
    real = os.replace

    def flaky(src, dst):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("in use")
        real(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    files.append_record(log, {"i": 1}, max_bytes=20)
    assert len(calls) == 3
    assert [r["i"] for r in files.read_records(log)] == [0, 1]


def test_a_line_longer_than_the_cap_is_refused(tmp_path):
    log = tmp_path / "r.jsonl"
    with pytest.raises(ValueError, match="longer than"):
        files.append_record(log, {"x": "y" * 100}, max_bytes=50)
    assert not log.exists()


APPENDER = textwrap.dedent("""
    import sys
    from moxi import files
    for i in range(int(sys.argv[3])):
        files.append_record(sys.argv[1], {"w": sys.argv[2], "i": i, "pad": "p" * 40},
                            max_bytes=4000)
""")


def test_concurrent_appenders_write_whole_lines(tmp_path):
    log = tmp_path / "r.jsonl"
    procs = [subprocess.Popen([sys.executable, "-c", APPENDER, str(log), str(w), "60"])
             for w in range(4)]
    for proc in procs:
        assert proc.wait(60) == 0
    for name in ("r.jsonl", "r.jsonl.1"):
        text = (tmp_path / name).read_text()
        assert text.endswith("\n")
        for line in text.splitlines():
            assert json.loads(line)["pad"] == "p" * 40
        # The size check before an append takes no lock, so writers racing at the cap
        # can each add a line past it; never more than one line per writer.
        assert len(text.encode()) <= 4000 + 4 * 80


def test_read_records_tail_reads_the_older_generation_when_short(tmp_path):
    log = tmp_path / "r.jsonl"
    (tmp_path / "r.jsonl.1").write_text("".join(f'{{"i":{i}}}\n' for i in range(5)))
    log.write_text('{"i":5}\n{"i":6}\n')
    assert files.read_records(log, tail=1) == [{"i": 6}]
    assert files.read_records(log, tail=2) == [{"i": 5}, {"i": 6}]
    assert files.read_records(log, tail=4) == [{"i": i} for i in range(3, 7)]
    assert files.read_records(log, tail=100) == [{"i": i} for i in range(7)]
    assert files.read_records(log) == [{"i": i} for i in range(7)]
    assert files.read_records(log, tail=0) == []


def test_read_records_skips_a_torn_line_and_non_objects(tmp_path):
    log = tmp_path / "r.jsonl"
    log.write_bytes(b'{"i":1}\n[1,2]\n\nnot json\n"text"\n{"i":2}\n{"i":3, "tor')
    assert files.read_records(log) == [{"i": 1}, {"i": 2}]
    assert files.read_records(log, tail=1) == [{"i": 2}]


def test_read_records_of_a_missing_or_unended_file(tmp_path):
    assert files.read_records(tmp_path / "none.jsonl") == []
    one = tmp_path / "one.jsonl"
    one.write_bytes(b'{"i":1}')
    assert files.read_records(one) == []


def test_read_records_across_block_boundaries(tmp_path):
    log = tmp_path / "r.jsonl"
    big = "b" * (files.BLOCK // 3)
    records = [{"i": i, "big": big} for i in range(12)]
    log.write_text("".join(json.dumps(r) + "\n" for r in records) + '{"torn": "' + big)
    assert files.read_records(log) == records
    assert files.read_records(log, tail=5) == records[-5:]


def test_read_records_tail_reads_only_the_end(tmp_path, monkeypatch):
    log = tmp_path / "r.jsonl"
    log.write_text("".join(json.dumps({"i": i, "pad": "x" * 100}) + "\n"
                           for i in range(5000)))
    reads = []
    real_open = open

    class Counting:
        def __init__(self, fh):
            self.fh = fh

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.fh.close()

        def seek(self, *a):
            return self.fh.seek(*a)

        def read(self, n):
            reads.append(n)
            return self.fh.read(n)

    monkeypatch.setattr(files, "open", lambda p, m="r", *a, **k:
                        Counting(real_open(p, m, *a, **k)), raising=False)
    assert files.read_records(log, tail=3)[-1]["i"] == 4999
    assert sum(reads) <= files.BLOCK


# -- private_dir -------------------------------------------------------------------------

@POSIX
def test_private_dir_makes_a_0700_directory(tmp_path):
    made = files.private_dir(tmp_path / "a" / "b")
    assert made == tmp_path / "a" / "b"
    assert made.is_dir() and mode_of(made) == 0o700


@POSIX
def test_private_dir_clears_group_and_other_bits(tmp_path):
    existing = tmp_path / "d"
    existing.mkdir()
    os.chmod(existing, 0o755)
    files.private_dir(existing)
    assert mode_of(existing) == 0o700


@POSIX
def test_private_dir_refuses_another_users_directory(tmp_path, monkeypatch):
    existing = tmp_path / "d"
    existing.mkdir()
    monkeypatch.setattr(os, "getuid", lambda: os.stat(existing).st_uid + 1)
    with pytest.raises(PermissionError, match="owned by"):
        files.private_dir(existing)


@POSIX
def test_private_dir_refuses_a_file(tmp_path):
    (tmp_path / "f").write_text("")
    with pytest.raises((NotADirectoryError, FileExistsError)):
        files.private_dir(tmp_path / "f")


@POSIX
def test_private_dir_refuses_a_symbolic_link_and_leaves_its_target(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    os.chmod(target, 0o755)
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(PermissionError, match="symbolic link"):
        files.private_dir(link)
    assert mode_of(target) == 0o755


@POSIX
def test_private_dir_refuses_a_dangling_symbolic_link_and_makes_nothing(tmp_path):
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "nowhere")
    with pytest.raises(PermissionError, match="symbolic link"):
        files.private_dir(link)
    assert link.is_symlink() and not (tmp_path / "nowhere").exists()


# -- the directories made on the way to a file -------------------------------------------

@pytest.fixture
def open_umask():
    """A umask that takes no bits away, so a directory made with the default mode is
    plainly open to everyone."""
    old = os.umask(0)
    yield
    os.umask(old)


def _take_lock(target: Path) -> None:
    with files.lock(target / "f.lock"):
        pass


MAKERS = {
    "atomic_write": lambda target: files.atomic_write(target / "f", "x"),
    "lock": _take_lock,
    "append_record": lambda target: files.append_record(target / "log.jsonl", {"a": 1}),
    "private_dir": files.private_dir,
}


@POSIX
@pytest.mark.parametrize("maker", MAKERS)
def test_each_missing_directory_on_the_way_is_made_0700(tmp_path, open_umask, maker):
    shared = tmp_path / "shared"
    shared.mkdir()
    os.chmod(shared, 0o775)
    target = shared / "a" / "b" / "c"
    MAKERS[maker](target)
    for each in (shared / "a", shared / "a" / "b", target):
        assert mode_of(each) == 0o700, each
    assert mode_of(shared) == 0o775


@POSIX
def test_making_directories_leaves_an_existing_one_as_it_is(tmp_path, open_umask):
    middle = tmp_path / "a" / "b"
    middle.mkdir(parents=True)
    os.chmod(tmp_path / "a", 0o751)
    os.chmod(middle, 0o755)
    files.atomic_write(middle / "c" / "f", "x")
    assert mode_of(tmp_path / "a") == 0o751
    assert mode_of(middle) == 0o755
    assert mode_of(middle / "c") == 0o700


@POSIX
def test_a_directory_made_without_privacy_gets_the_default_mode(tmp_path, open_umask):
    files._make_dirs(tmp_path / "a" / "b", private=False)
    assert mode_of(tmp_path / "a") == 0o777
    assert mode_of(tmp_path / "a" / "b") == 0o777


def test_making_directories_refuses_a_file_in_the_way(tmp_path):
    (tmp_path / "a").write_text("")
    with pytest.raises(FileExistsError):
        files._make_dirs(tmp_path / "a")


@WINDOWS
def test_private_dir_on_windows_grants_only_this_user(tmp_path):
    made = files.private_dir(tmp_path / "d")
    assert made.is_dir()
