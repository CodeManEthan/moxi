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
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

TESTS = Path(__file__).resolve().parent

SITECUSTOMIZE = textwrap.dedent('''\
    # Put the tests' fake supervisor in services' page server, and nowhere else.
    import sys

    _argv = list(getattr(sys, "orig_argv", ()))
    if "moxi_loopback.serve" in _argv and _argv[_argv.index("moxi_loopback.serve") + 1:][:1] == ["services"]:
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
        for _module in (acts, lifecycle, state, verdict):
            _module.units = _fake
''')


def fake_site(base: Path, spec: dict[str, Any]) -> Path:
    """A site directory for the shell's `PYTHONPATH` whose `sitecustomize` gives services'
    page server a fake supervisor holding `spec`'s units: `written` by row name and
    `modules` by `<module>/<name>`, each a `fake_units.Unit`'s fields."""
    site = base / "site"
    site.mkdir(parents=True)
    path = base / "units.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    (site / "sitecustomize.py").write_text(
        SITECUSTOMIZE.format(tests=str(TESTS), spec=str(path)), encoding="utf-8")
    return site


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


def get(shell, path: str) -> Any:
    """GET `path` on services' page, through the shell, signed in with no browser."""
    status, data = shell.session().request("GET", f"/m/services/{path}")
    assert status == 200, (path, status, data)
    return data


def written(name: str, **fields: Any) -> dict[str, Any]:
    """A written daemon row's fields, with `fields` laid over them."""
    base: dict[str, Any] = dict(name=name, form="written", kind="daemon",
                                argv=["/usr/bin/python3", "-m", "http.server"],
                                platform=sys.platform, enabled=True)
    base.update(fields)
    return base
