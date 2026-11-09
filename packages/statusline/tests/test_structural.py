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

#: Run in a child: the render's `main` as the console script calls it, then what it left
#: in `sys.modules`, on stderr after the line. Before the render, what the standard
#: library's TOML reader loads by itself, which the config read can't do without:
#: `tomllib` imports `datetime`.
PROBE = """
import sys
import tomllib
sys.stderr.write("TOML " + repr(sorted(sys.modules)) + "\\n")
from moxi_statusline.render import main
main([])
sys.stderr.write("MODULES " + repr(sorted(sys.modules)) + "\\n")
"""

FORBIDDEN = ("subprocess", "socket", "logging", "datetime", "moxi_sessions")


def probe(payload, argv: str = "[]") -> tuple[list[str], list[str]]:
    """`(what tomllib alone loads, what the render left loaded)`."""
    done = subprocess.run([sys.executable, "-c", PROBE.replace("main([])", f"main({argv})")],
                          input=json.dumps(payload).encode(), capture_output=True,
                          timeout=60, check=True)
    said = done.stderr.decode().splitlines()

    def read(tag):
        line = [ln for ln in said if ln.startswith(tag + " ")]
        return ast.literal_eval(line[-1].removeprefix(tag + " "))

    return read("TOML"), read("MODULES")


def loaded(payload, argv: str = "[]") -> list[str]:
    return probe(payload, argv)[1]


def test_the_default_line_loads_no_api_and_nothing_a_segment_may_not():
    now_ish = 4_000_000_000
    payload = {**INPUT, "rate_limits": {
        "five_hour": {"used_percentage": 20, "resets_at": now_ish}}}
    toml, names = probe(payload)
    assert [n for n in names if n.startswith("moxi_") and n.endswith(".api")] == []
    # The TOML reader's own imports are the config read's price; the render adds none.
    assert {n for n in toml if n.split(".")[0] in FORBIDDEN} <= {"datetime"}
    assert [n for n in names if n.split(".")[0] in FORBIDDEN and n not in toml] == []
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
