"""What a render loads: no API module, nothing a segment may not load, and every module of
the core or of this package on `RENDER_IMPORTS`. No timing here: a render's time is
measured on its own, not in the suite."""

import ast
import json
import os
import subprocess
import sys

from statusline_kit import INPUT

from moxi import modules
from moxi_statusline.render import RENDER_IMPORTS

#: The standard library modules the render imports whose own imports are outside the
#: rule: `tomllib` imports `datetime`, and before Python 3.13 `pathlib` imports
#: `urllib.parse`. What they load by themselves is measured, not listed.
STDLIB = ("tomllib", "pathlib")

#: Run in a child: those modules, then what they left in `sys.modules`; then the render's
#: `main` as the console script calls it, with every import statement the render's own
#: modules and its segments run recorded, and what the render left in `sys.modules`. Each
#: on stderr after the line.
PROBE = """
import builtins
import sys
for name in {stdlib!r}:
    __import__(name)
sys.stderr.write("STDLIB " + repr(sorted(sys.modules)) + "\\n")
real = builtins.__import__
asked = set()
def counted(name, globals=None, locals=None, fromlist=(), level=0):
    who = globals.get("__name__", "") if globals else ""
    if level == 0 and who.startswith("moxi"):
        asked.add(name)
        asked.update(name + "." + item for item in fromlist or ())
    return real(name, globals, locals, fromlist, level)
builtins.__import__ = counted
from moxi_statusline.render import main
main({argv})
builtins.__import__ = real
sys.stderr.write("ASKED " + repr(sorted(asked)) + "\\n")
sys.stderr.write("MODULES " + repr(sorted(sys.modules)) + "\\n")
"""

FORBIDDEN = ("subprocess", "socket", "logging", "datetime", "moxi_sessions")
#: Modules a default render doesn't use, which a module on its path once loaded at import:
#: each costs several milliseconds on every render. The render's own modules ask for none
#: of them, and the render loads none beyond what `STDLIB` loads by itself.
NEVER = ("urllib.parse", "dataclasses")


def probe(payload, argv: str = "[]") -> tuple[list[str], list[str], list[str]]:
    """`(what STDLIB alone loads, what the render's modules import, what it left loaded)`."""
    done = subprocess.run([sys.executable, "-c", PROBE.format(stdlib=STDLIB, argv=argv)],
                          input=json.dumps(payload).encode(), capture_output=True,
                          timeout=60, check=True)
    said = done.stderr.decode().splitlines()

    def read(tag):
        line = [ln for ln in said if ln.startswith(tag + " ")]
        return ast.literal_eval(line[-1].removeprefix(tag + " "))

    return read("STDLIB"), read("ASKED"), read("MODULES")


def loaded(payload, argv: str = "[]") -> list[str]:
    return probe(payload, argv)[2]


def test_the_default_line_loads_no_api_and_nothing_a_segment_may_not():
    now_ish = 4_000_000_000
    payload = {**INPUT, "rate_limits": {
        "five_hour": {"used_percentage": 20, "resets_at": now_ish}}}
    stdlib, asked, names = probe(payload)
    assert [n for n in names if n.startswith("moxi_") and n.endswith(".api")] == []
    # The standard library's own imports are the price of what the render needs from it;
    # of the forbidden modules that is only `datetime`, and the render adds none.
    assert {n for n in stdlib if n.split(".")[0] in FORBIDDEN} <= {"datetime"}
    added = [n for n in names if n not in stdlib]
    assert [n for n in added if n.split(".")[0] in FORBIDDEN] == []
    assert [n for n in added if n in NEVER] == []
    # Where the standard library already loads one of these, only the import statements
    # show the render asking for it too.
    assert [n for n in asked if n in NEVER or n.split(".")[0] in FORBIDDEN] == []
    ours = [n for n in names if n.split(".")[0] in ("moxi", "moxi_statusline")]
    assert sorted(set(ours) - set(RENDER_IMPORTS)) == []
    # The default line's segments, where their modules are installed here.
    for name in ("usage", "cache"):
        found = modules.announced(name)
        if found.state == modules.PRESENT and found.segment:
            assert found.segment in names


def test_a_line_of_base_fields_loads_no_discovery_and_no_segment():
    names = loaded({**INPUT})
    assert "moxi.modules" in names  # the default show names modules
    names = loaded(INPUT, "['--show', 'dir,model']")
    assert "moxi.modules" not in names
    assert [n for n in names if n.endswith(".segment")] == []


def test_every_render_import_is_a_module_here():
    import importlib.util

    for name in RENDER_IMPORTS:
        assert importlib.util.find_spec(name) is not None, name


def test_the_hook_is_in_before_the_config_is_read():
    probe = """
import sys
from moxi import render as hook
from moxi_statusline import settings
real = settings.load
def load(path=None):
    sys.stderr.write(f"HOOKED {hook.installed()} {sys.dont_write_bytecode}\\n")
    return real(path)
settings.load = load
from moxi_statusline.render import main
main([])
"""
    done = subprocess.run([sys.executable, "-c", probe], input=json.dumps(INPUT).encode(),
                          capture_output=True, timeout=60, check=True,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": ""})
    assert "HOOKED True True" in done.stderr.decode()
