"""What services' browser tests share: a test shell whose services page server acts on a
fake supervisor, rows seeded into the store that server reads, and reads of what the
server answers.

The test shell's page servers refuse every real supervisor call (it sets
`PYTEST_CURRENT_TEST`), and its runtime directory has no systemd socket, so services
there sees no supervisor at all. To show real verdicts and let acts end `done`, the
shell's site holds a `sitecustomize` that, in services' page server and nowhere else,
puts `fake_units.FakeUnits` in place of the core's writer, the same fake the in-process
tests use. Its units start from a JSON file the test module writes, and live in that
server's memory, so a new server (after an idle exit) starts from the file again.

Rows go into services' store with the shell's environment, so the store is the one the
page server reads, under the shell's home. A `moxi` command run here sees no supervisor,
so tests read what the page shows back from the page server (`get`), or from the store.

The same `sitecustomize` can give that server a fake slots API and fixed health checks,
so no test reaches a tmux server or a shell's port: with `slots`, each read of slots'
API answers from a JSON file (`slots_said`), and `health` gives a module daemon's check
by its id.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import weakref
from pathlib import Path
from typing import Any

import pytest

#: Every browser test module's marks. The bound is kept by a thread that, past it, prints
#: every thread's stack and ends the test's process: under xdist the run reports the worker
#: crashed while running that test, and goes on in a new worker. A signal raises once
#: inside the test, and a teardown that then waits on a browser that stopped answering
#: would hold the worker with no end.
MARKS = [pytest.mark.browser, pytest.mark.timeout(180, method="thread"),
         pytest.mark.skipif(not sys.platform.startswith("linux"),
                            reason="services' page is checked in a browser on Linux; the "
                                   "Windows and macOS runs are owed")]

TESTS = Path(__file__).resolve().parent

SITECUSTOMIZE = textwrap.dedent('''\
    # Put the tests' fake supervisor in services' page server, and nowhere else.
    import sys

    _argv = list(getattr(sys, "orig_argv", ()))
    if "moxi_ui.loopback.serve" in _argv and _argv[_argv.index("moxi_ui.loopback.serve") + 1:][:1] == ["services"]:
        import json

        sys.path.insert(0, {tests!r})
        from fake_units import FakeUnits, Unit

        from moxi_services import acts, lifecycle, state, verdict

        _fake = FakeUnits()
        with open({spec!r}, encoding="utf-8") as _file:
            _spec = json.load(_file)
        for _name, _unit in _spec.get("written", {{}}).items():
            _fake.written[_name] = Unit(**_unit)
        for _key, _unit in _spec.get("modules", {{}}).items():
            _fake.modules[tuple(_key.split("/", 1))] = Unit(**_unit)
        for _ref, _unit in _spec.get("foreign", {{}}).items():
            _fake.foreign[_ref] = Unit(**_unit)
        _fake.fail = {{tuple(_pair) for _pair in _spec.get("fail", [])}}
        for _module in (acts, lifecycle, state, verdict):
            _module.units = _fake

        _checks = {{_id: tuple(_said) for _id, _said in _spec.get("health", {{}}).items()}}
        _health = state.health

        def _checked(row, st):
            if row.id in _checks and st is not None and st.state == "active":
                return _checks[row.id]
            return _health(row, st)

        state.health = _checked

        if _spec.get("installed") is not None:
            import moxi.modules

            _installed, _kept = moxi.modules.installed, set(_spec["installed"])
            moxi.modules.installed = lambda: [e for e in _installed() if e.name in _kept]

        if _spec.get("slots"):
            import types

            import fake_slots

            def _said():
                with open(_spec["slots"], encoding="utf-8") as _file:
                    return json.load(_file)

            def _list_slots():
                said = _said()
                return fake_slots.Listing(
                    ok=said.get("ok", True), why=said.get("why", ""),
                    slots=tuple(fake_slots.Slot(**one) for one in said.get("slots", [])))

            def _jobs():
                return [fake_slots.Job(**one) for one in _said().get("jobs", [])]

            _api = types.ModuleType("moxi_slots.api")
            _api.list_slots, _api.jobs = _list_slots, _jobs
            sys.modules["moxi_slots.api"] = _api
''')


def fake_site(base: Path, spec: dict[str, Any]) -> Path:
    """A site directory for the shell's `PYTHONPATH` whose `sitecustomize` gives services'
    page server a fake supervisor holding `spec`'s units: `written` by row name,
    `modules` by `<module>/<name>` and `foreign` (units not yet adopted) by the
    supervisor's own name, each a `fake_units.Unit`'s fields, and `fail`, the
    `[act, name]` pairs whose act fails. `health` gives a running module daemon's check
    by its id, `[verdict, line]`, in place of the module's own. With `slots` true, slots'
    API answers from `<base>/slots.json`, which `slots_said` writes. `installed`, when
    given, names the only modules the page server finds installed, as on a small install."""
    site = base / "site"
    site.mkdir(parents=True)
    path = base / "units.json"
    if spec.get("slots"):
        spec = {**spec, "slots": str(slots_said(base))}
    path.write_text(json.dumps(spec), encoding="utf-8")
    (site / "sitecustomize.py").write_text(
        SITECUSTOMIZE.format(tests=str(TESTS), spec=str(path)), encoding="utf-8")
    return site


def slots_said(base: Path, *, ok: bool = True, why: str = "", slots: tuple = (),
               jobs: tuple = ()) -> Path:
    """What the fake slots API answers from now on, for the site `fake_site` made under
    `base`: a listing (`ok`, `why`, `slots`, each a `fake_slots.Slot`'s fields) and its
    `jobs` (each a `fake_slots.Job`'s)."""
    path = base / "slots.json"
    path.write_text(json.dumps({"ok": ok, "why": why, "slots": list(slots), "jobs": list(jobs)}),
                    encoding="utf-8")
    return path


_SEED = textwrap.dedent('''\
    import json, sys
    from moxi_services import store

    for fields in json.loads(sys.argv[1]):
        after = fields.pop("after", {})
        store.write(lambda conn: store.insert(conn, by="test", **fields))
        if after.get("stopped"):
            store.write(lambda conn: store.set_stopped(conn, fields["name"], after["stopped"], by="test"))
        if after.get("removing"):
            store.write(lambda conn: store.set_removing(conn, fields["name"], by="test"))
''')

_NAMES = textwrap.dedent('''\
    import json
    from moxi_services import store

    print(json.dumps(sorted(row.name for row in store.read_rows())))
''')


def python(shell, code: str, *args: str) -> str:
    """Runs `code` with the shell's environment, as a terminal under its home would, and
    returns what it printed."""
    done = subprocess.run([sys.executable, "-c", code, *args], env=shell.env,
                          cwd=shell.env["HOME"], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=60)
    if done.returncode != 0:
        raise AssertionError(f"the store script failed ({done.returncode}):\n"
                             f"{done.stdout}{done.stderr}")
    return done.stdout


def seed(shell, *rows: dict[str, Any]) -> None:
    """Inserts each row's fields into services' store; a row's `after` may set
    `stopped` (a time) and `removing`."""
    python(shell, _SEED, json.dumps(list(rows)))


def stored(shell) -> list[str]:
    """The names of the rows in services' store."""
    return json.loads(python(shell, _NAMES))


def moxi(shell, *args: str) -> subprocess.CompletedProcess:
    """`moxi ARGS` with the shell's environment."""
    done = subprocess.run([sys.executable, "-m", "moxi", *args], env=shell.env,
                          cwd=shell.env["HOME"], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise AssertionError(f"moxi {' '.join(args)} failed ({done.returncode}):\n"
                             f"{done.stdout}{done.stderr}")
    return done


#: One signed-in session per shell for the reads a test makes without a browser. Each
#: sign-in is a session, and the shell keeps at most 20, dropping the oldest first: a new
#: one per read would soon drop the browser's own.
_SESSIONS: weakref.WeakKeyDictionary[Any, Any] = weakref.WeakKeyDictionary()


def session(shell) -> Any:
    """The shell's one signed-in session for reads without a browser."""
    found = _SESSIONS.get(shell)
    if found is None or found.get("/m/ui/api/modules")[0] != 200:
        found = _SESSIONS[shell] = shell.session()
    return found


def get(shell, path: str) -> Any:
    """GET `path` on services' page, through the shell, signed in with no browser."""
    status, data = session(shell).request("GET", f"/m/services/{path}")
    assert status == 200, (path, status, data)
    return data


def written(name: str, **fields: Any) -> dict[str, Any]:
    """A written daemon row's fields, with `fields` laid over them."""
    base: dict[str, Any] = dict(name=name, form="written", kind="daemon",
                                argv=["/usr/bin/python3", "-m", "http.server"],
                                platform=sys.platform, enabled=True)
    base.update(fields)
    return base
