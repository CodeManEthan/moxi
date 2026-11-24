"""The sweep at a session's start: turns' `ready` starts it, detached, only when both
sweep settings are on and the last removing run began more than ten minutes ago, and
returns before it ends. The module turns reads at its start loads only the standard
library."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from importlib.metadata import entry_points

import pytest
from moxi_dev.testing import console_script

from moxi_rounds import listener, listeners, sweeping

AGO_S = {"never": None, "an hour": 3600, "five minutes": 300, "a garbled time": "garbled"}


def started(age):
    where = sweeping.started_file()
    where.parent.mkdir(parents=True, exist_ok=True)
    if age is None:
        return
    text = age if isinstance(age, str) else time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - age))
    where.write_text(text + "\n", encoding="utf-8")


@pytest.fixture
def spawned(monkeypatch):
    calls = []
    monkeypatch.setattr(listener, "_spawn", lambda: calls.append(True) or 0)
    return calls


@pytest.mark.parametrize("at_start", [True, False])
@pytest.mark.parametrize("armed", [True, False])
@pytest.mark.parametrize("ago", list(AGO_S))
def test_ready_starts_the_sweep_only_when_both_settings_are_on_and_it_is_due(
        write_config, spawned, at_start, armed, ago):
    write_config(f"[sweep]\nat_start = {str(at_start).lower()}\n"
                 f"armed = {str(armed).lower()}\n")
    started(AGO_S[ago])

    assert listener.ready(object()) is None

    assert spawned == ([True] if at_start and armed and ago != "five minutes" else [])


def test_a_config_that_cant_be_read_starts_nothing(write_config, spawned):
    write_config("[sweep]\ngrace_hours = 0\n")
    listener.ready(object())
    assert spawned == []


def test_ready_returns_before_the_sweep_ends_and_passes_no_git_variable(
        write_config, monkeypatch, tmp_path):
    write_config("[sweep]\nat_start = true\narmed = true\n")
    out = tmp_path / "stand-in.out"
    stand_in = console_script(
        tmp_path, "moxi",
        "import os, sys, time\n"
        f"open({str(out)!r}, 'w').write(' '.join(sys.argv[1:]) + '\\n' + "
        "' '.join(sorted(name for name in os.environ if name.startswith('GIT_'))) + "
        "'\\n' + str(os.getpid()) + '\\n')\ntime.sleep(60)\n")
    import moxi.paths

    monkeypatch.setattr(moxi.paths, "script", lambda name: stand_in)
    monkeypatch.setenv("GIT_DIR", str(tmp_path))
    began = time.monotonic()

    listener.ready(object())
    took_s = time.monotonic() - began

    for _ in range(100):
        if out.is_file() and out.read_text(encoding="utf-8").count("\n") == 3:
            break
        time.sleep(0.05)
    argv, git_names, pid = out.read_text(encoding="utf-8").split("\n")[:3]
    os.kill(int(pid), 9)
    assert took_s < 5
    assert argv == " ".join(listener.ARGV) and git_names == ""


def test_the_entry_point_names_a_module_of_the_standard_library_only():
    [entry] = [one for one in entry_points(group="moxi.turns.listeners")
               if one.name == "rounds"]
    assert entry.value == "moxi_rounds.listeners"
    code = ("import sys; before = set(sys.modules); import moxi_rounds.listeners; "
            "print('\\n'.join(sorted(set(sys.modules) - before)))")
    loaded = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                            text=True, check=True).stdout.split()
    outside = [name for name in loaded
               if name.partition(".")[0] not in sys.stdlib_module_names
               and name not in ("moxi_rounds", "moxi_rounds.listeners")]
    assert outside == []
    assert listeners.LISTENERS == ((("ready",), "moxi_rounds.listener:ready"),)
