"""Moxi's test kit: a pytest plugin that fences every test in, and the helpers tests share.

A package loads it from its root `conftest.py`:

    pytest_plugins = ["moxi_dev.testing"]

Installing moxi-dev arms nothing by itself (there is no `pytest11` entry point); only a
package that names the plugin is fenced.

The fence, around every test (the autouse fixture `fence`):

- The environment. Emptied to `PATH`, the locale (`LANG`, `LANGUAGE`, `LC_*`), the
  variables pytest and xdist set for themselves (`PYTEST_CURRENT_TEST`, `PYTEST_VERSION`,
  `PYTEST_XDIST_WORKER`, `PYTEST_XDIST_WORKER_COUNT`, `PYTEST_XDIST_TESTRUNUID`) and
  `MOXI_TEST_REAL_SUPERVISOR`, which opts a run into the system's real supervisor: a
  test sees it at call time as pytest started with it, and doesn't when it was unset. Then
  every harness home a package reads is pointed into the test's tmp: `HOME` and the XDG
  homes under `tmp_path / "home"`, `CLAUDE_CONFIG_DIR` and `CODEX_HOME` inside it, and
  `XDG_RUNTIME_DIR` and each installed module's `MOXI_<MODULE>_HOME` in a short
  per-test directory, made inside the run's short directory (see `short_dir` and The
  run, below), so a socket under them keeps a short path (on Windows the module homes
  are in the test's profile instead; see below). The run's child keys and
  `PLAYWRIGHT_BROWSERS_PATH` are carried over from the run's environment (The run).
  `TMPDIR`, and `tempfile`'s own default with it, point into that short directory too,
  so a temp file the test or a child makes lands in the test's tmp and goes with it.
  `MOXI_TEST_FENCED` holds the same directory: a pytest run that starts with it in its
  environment is nested in this test (Temp files, below).
  OpenCode reads its homes through `XDG_*`; its content variables (`OPENCODE_*`) are
  cleared with the rest. A package that starts reading another home adds it to
  `HARNESS_HOMES`.
  On Windows the fence does the same for the user's own folders, so no test sees or
  writes the real profile: `USERPROFILE` is the test's home, `HOMEDRIVE` and `HOMEPATH`
  are that home split at its drive, `LOCALAPPDATA` and `APPDATA` are under it, and so
  is each per-user folder platformdirs reads through a `WIN_PD_OVERRIDE_*` variable
  (without one it asks Windows for the real folder, whatever `APPDATA` says); see
  `WINDOWS_HOMES`. Each module's `MOXI_<MODULE>_HOME` is `moxi\\<module>` under the
  test's `LOCALAPPDATA`, so whatever a module keeps only inside the user's profile (comms'
  token store) is inside the test's own. Every directory the fence gives is spelled in
  its long form, its real path: the runner's `TEMP` may be spelled with an 8.3 short name
  (`C:\\Users\\RUNNER~1\\...`, noqa: MX6 -- GitHub's Windows runner profile), and a
  test compares the fence's directories with paths Windows hands back resolved. `TEMP`
  and `TMP` point where `TMPDIR` does. The system's own variables a process needs
  (`SYSTEMROOT`, `PATHEXT`, `COMSPEC` and the rest of `WINDOWS_KEPT_ENV`) are kept.
  Off Windows none of these is set or kept.
  `MOXI_ANCHOR_HOME` stands in for the account's own folder, which the core reads from
  the user database or the shell and no other variable moves: it is the test's home (on
  Windows the test's `LOCALAPPDATA`), so the core's anchor, and the record of the
  terminal's environment kept there, are the test's own and never the real account's.
  The `terminal_record` fixture writes such a record.
- Byte code. `sys.dont_write_bytecode` is set for the test, so a module first imported
  inside it is compiled in memory and nothing is written next to its source.
- Writes. An audit hook (`sys.addaudithook`) refuses, with `FenceError`, a write whose
  path resolves outside the test's own tmp: the run's pytest temp root (`--basetemp`,
  else the `pytest-of-<user>/pytest-<n>` directory, or under xdist the worker's own
  directory in it), which holds the test's `tmp_path` and whatever `tmp_path_factory`
  makes, and the test's short directory, with every directory `short_dir` has made and
  not yet removed. The rest of the system temp directories (other runs, other
  processes' files) is outside. Besides those it lets through, and only these:
  the null device, `/dev/tty` and `/dev/pts/*` (terminals). The null device is
  `os.devnull`; on Windows it is either spelling a write to `nul` resolves to:
  `\\\\.\\nul`, and a drive or UNC path whose last part is `nul` (a relative `nul`
  resolves to one in the working directory), which Windows opens as the device. A
  `\\\\?\\` or `\\\\.\\` path to a file named `nul` creates a real file, so it is
  refused; so are `nul.txt` and `nul:`, which only some Windows versions open as the
  device. On Windows a long-form path (`\\\\?\\C:\\...`, which pytest uses to remove its
  temp directories) is judged as the plain path it names, so it is let through where
  that path is; one that names no plain path (a `..` past the prefix) is refused.
  A write is: `open` for writing or
  creating, `os.mkdir`, `os.rename`/`os.replace` (both ends), `os.remove`, `os.rmdir`,
  `os.symlink` and `os.link` (the new name), `os.truncate`, `os.chmod`, `os.chown`,
  `os.utime`, `os.setxattr`, `os.removexattr`, `shutil.rmtree`, `shutil.copyfile`,
  `shutil.copytree`, `shutil.move` (the destination), `sqlite3.connect` to a file
  (not `:memory:`, not a `mode=ro` or `mode=memory` URI), `tempfile.mkstemp`,
  `tempfile.mkdtemp`, and `socket.bind` to a filesystem `AF_UNIX` path.
- The network. The same hook refuses `socket.connect`, `socket.sendto` and
  `socket.sendmsg` to an `AF_INET`/`AF_INET6` address that is not loopback (`127.0.0.0/8`,
  `::1`, `::ffff:127.*`, the unspecified address, `localhost` and `*.localhost`; any
  other host name is refused, though Python has looked it up before the hook sees the
  connect, so a lookup alone is not fenced), and a connect to a filesystem `AF_UNIX`
  socket outside the allowed roots (a real daemon's socket, a tmux or X11 socket under
  `/tmp`). A `live` test lifts the network part, both the address check and the
  `AF_UNIX` connect check; its writes, a `bind` included, stay fenced.

The hook can't be removed, so it checks a flag the fixture raises for the test's setup,
call and teardown and lowers after. The fixture is function-scoped, so a fixture of any
wider scope (class, module, package or session) runs outside every test's fence: it sees
the run's environment (The run, below), and the hook doesn't judge the writes it makes in
process. A pytest run inside a test, in process
(`pytester.runpytest()`), fences its own tests and puts the outer test's fence back
when it ends. A refusal
raises `FenceError` (a `PermissionError`) where the write or connect was tried and is
also recorded, so a test that catches the error still fails. The hook sees this process
only: a subprocess gets the fenced environment, through the seam (The run), but not the
hook. Paths are judged after
symlinks resolve (a link inside tmp to a place outside is outside); a name relative to a
`dir_fd` is resolved through `/proc/self/fd` and let through where that can't be read.

The run. When the test loop starts, after collection and before the first fixture of any
scope sets up (a wrapper on `pytest_runtestloop`), the kit puts the run's environment in
its own process, and puts the started one back when the loop ends. So every fixture of any
scope, and every process it starts, sees the run's environment, never the account's. It
is what a test's fence builds, from the environment pytest started with: the homes under
run-home in the run's pytest temp root (under xdist, the worker's), and the runtime
directory, each module's home and the temp directory (`TMPDIR`, `tempfile`'s default,
and on Windows `TEMP` and `TMP`) in the run's short directory, which `MOXI_TEST_FENCED`
names, so a pytest a fixture starts as a child is nested (Temp files). It also carries
the child keys below, and `PLAYWRIGHT_BROWSERS_PATH`, the browsers folder the started
environment finds, which Playwright reads and no test writes. Collection runs before the
loop and sees the started environment; `started_env()` gives it, inside the loop too, to
a test or fixture that reads the account's environment on purpose (an opt-in tier).

The run's roots are its pytest temp root, its short directory and its one uv cache. The
short directory holds every short directory the loop makes: each test's, and each
`short_dir()` a fixture of wider scope makes (`short_root()` names it outside a test). It
is removed when the loop ends. A process that outlives the test that started it still
writes inside the roots, since every place its environment names is in them until then.

The child keys, made once when the loop starts:

- `UV_CACHE_DIR`, the run's one uv cache, and `UV_PYTHON_INSTALL_DIR`,
  `UV_PYTHON_BIN_DIR`, `UV_TOOL_DIR` and `UV_TOOL_BIN_DIR`, folders under run-uv in the
  run's temp root, so uv never falls back to a default (on Windows uv may take one from
  the shell's known folders, whatever `LOCALAPPDATA` says);
- `UV_PYTHON`, the suite's interpreter, and `UV_PYTHON_DOWNLOADS=never`, so every uv a
  test reaches, the product's own included, runs the suite's Python and installs none;
- `PYTHONDONTWRITEBYTECODE=1`, so a Python child writes no byte code, unless the same
  environment sets `PYTHONPYCACHEPREFIX` to a folder under the roots.

The run's uv cache is the run's own, new each run, in the run's temporary folder: uv-cache
in its pytest temp root, or under xdist in the controller's, beside the workers' own temp
roots. The controller makes it and hands its path to each worker, so the workers share it
(uv locks its cache) and a run downloads each package once. It goes when pytest removes
that temp root (pytest keeps the last three runs'). No setting points a run at the
account's uv cache, so a test that installs a third-party package needs the network.

uv in a child: the `uv_env` fixture is the fenced environment, a copy, which holds the
child keys; a test may pass it as its child's environment, or pass none. An environment a
fixture builds itself keeps any uv folder it sets under the roots.

The seam. While the loop runs, `subprocess.Popen` starts every child through the kit, so
every `subprocess` and `asyncio` child passes it. A child given no `env` inherits the
run's environment or the test's, which holds every key, and so does whatever it starts
from its own. To an `env` the caller passes, the seam adds each child key it lacks, a
temp directory and `HOME` it lacks, and on Windows each folder of `WINDOWS_HOMES` it
lacks, laid out under its own home where it names one (`USERPROFILE`, else `HOME`), else
this process's. It refuses, with `FenceError`, an `env` that:

- sets a uv folder of the child keys outside the roots;
- turns byte code on (`PYTHONDONTWRITEBYTECODE` unset or empty) with no
  `PYTHONPYCACHEPREFIX` under the roots;
- sets a variable the fence moves (a home, `XDG_RUNTIME_DIR`, the temp directory,
  `MOXI_HOME`, a module's home) to a path that exists outside the roots. A path that
  names nothing on disk (`/nowhere`) passes. A `live` test, and a run that started with
  `MOXI_TEST_REAL_SUPERVISOR=1`, may name the account's own here; the rest still holds.

A Python child started isolated (`-I` or `-E` among its interpreter options) ignores both
byte-code variables. So to an argument list whose first word is a Python interpreter (the
suite's, or a file whose name starts with `python`) with such an option, the seam adds
`-X pycache_prefix=` naming the pycache folder in the run's temp root after the
interpreter, unless the list gives `-B` or a prefix of its own; a prefix of its own
outside the roots is refused. A shell command string gets no prefix.

For the whole loop, not only a test's span, the audit hook judges an environment passed
to `os.exec*`, `os.spawn*` or `os.posix_spawn` as the seam judges one, and also refuses
one that lacks a child key; it can add nothing there.

The first loop in a process owns the run. A loop that starts while it runs (pytester's
in-process run) borrows it: it makes and removes nothing, and puts the kit's state and
this process's environment back when it ends; `started_env()` inside it is the outer
test's environment when it started the inner run. xdist's controller runs the loop but
no test, so it puts nothing in place.

What the run leaves out: byte code the pytest process writes beside the source it imports
outside a test (chiefly at collection); an isolated Python child started some way the
seam doesn't see (a shell string, `tmux`, `os.exec*`); a process whose parent built its
environment afresh (`env -i`) rather than from the one it was handed; what a fixture of
wider scope writes in process to a path it names itself; processes started outside the
loop (collection, xdist's workers); and a program that asks Windows for a known folder
itself, with no variable that moves it, other than uv, platformdirs and the core.

Temp files. `MOXI_TEST_TEMP` (`disk`, the default, or `ram`) decides where a run keeps
pytest's temp root and its short directories (`moxi_dev.temp` holds the rules: on
Linux `/var/tmp` or `/dev/shm`; macOS and Windows keep the system's own, and `ram` there
runs as `disk` and says so). Only the controlling process of a top-level run chooses,
at its configure, after a sweep of what earlier runs left (`moxi_dev.temp.sweep`). It
puts its root in `PYTEST_DEBUG_TEMPROOT` for pytest and hands the whole choice to each
xdist worker through `workerinput`, so a worker never reads the setting, and the
`--basetemp` xdist gives it never counts as one given to the run. A `--basetemp` given
to the controller leaves the run to pytest: no root is set, nothing of pytest's is
removed and nothing is swept. A run is nested when a fence is active in its process
(pytester's in-process run) or its environment carries `MOXI_TEST_FENCED` (a child a
fenced test, or a fixture under the run's environment, started): it chooses nothing,
records nothing, sweeps nothing and puts its short directories in the outer test's short
directory, or the outer run's. pytest's header gets one
`moxi-dev temp:` line saying where the temp is and why, and one more for what a sweep
removed. The durations file gets the choice as its `temp` entry.

Each process of a run that isn't nested makes one short process dir at its configure,
`mx-` and 6 characters under the short root, with its owner record beside it (see
`moxi_dev.temp`), and removes it at its end, the record last. The run's short directory is made inside it
(The run), and every short directory the loop makes inside that; outside the loop
`short_root()` names the process dir. When the root is in memory, a test's `tmp_path` is
removed once its teardown ends unless a phase of the test failed, and the controller
removes the whole basetemp at the run's end, failed tests' files and browser traces
with it; the basetemp and each worker's are recorded so a later sweep can remove them
if the run is killed. On disk pytest's own retention stands.

Symlinks: Windows lets an account make one only with the symlink privilege or with
Developer Mode on. `can_symlink()` tries a real one once per process and keeps the
answer; a test that needs one asks for the `needs_symlinks` fixture, or calls
`need_symlinks()` at the point it needs one, and is skipped where the account may not.

The fsync seam: `os.fsync` (and `os.fdatasync`) is a no-op in every test that is not
marked `honest_fsync`; the no-op still refuses a closed descriptor. Code must call
`os.fsync(...)` through the module for the seam to reach it.

The port seam: before the core's unit plan takes a daemon's socket address, it asks
whether anything listens there with a TCP connect (`moxi.units._plan.answers`). In every
test not marked `honest_ports` or `live` that connect is replaced by one that answers
"nothing listens", so what happens to listen on this machine (a daemon on its default
port, another run's server) never decides a test. A test that wants a listener seen
either says so through its own stand-in or is marked `honest_ports` and listens for real.

Markers registered here: `browser`, `live`, `slow`, `build`, `touches`, `finding`,
`timed`, `honest_fsync` and `honest_ports`. A package's `addopts` deselects the four
tiers in `TIERS`; a `live` test also needs `MOXI_TEST_LIVE=1` in the environment pytest
starts with, else it is skipped.

Timed tests. A test that asserts a wall-clock budget (a median under so many
milliseconds, an import under its share of a hook's ceiling) is marked `timed`, whatever
its tier. On a loaded machine such a test measures the machine, so `moxi-dev check`
runs no timed test: it holds every one back, and `moxi-dev timed` runs them on their
own. A test whose bound only tells a
deadline or timeout that fired from one that never did (a 0.3 s timeout that must return
in under 0.9 s) splits in two. The land-check test carries no `timed`, keeps every
behaviour assertion, and holds five times the bound with no factor, so a guard that never
fired fails it and load alone doesn't. Its twin, named `<test>_in_time`, carries `timed`
and holds the bound times `timed_slack()`. A plain `pytest`, with neither of the values
below, runs a timed test where it falls, like any other.

`MOXI_TEST_TIMED_SLACK=<factor>`, read once from the environment pytest starts with, is
how much slower than the machine that set the budgets this one may be: a finite number
of at least 1, else the run stops; unset, 1. `timed_slack()` returns it, inside a fenced
test too. In a `timed` test, every upper bound on a wall-clock measurement of the run
itself (an elapsed time, a per-call cost, a median, a percentile) is multiplied by
`timed_slack()`. Lower bounds (a wait that must have happened), ratios between two
measurements of the same run and counts are left alone: a slower machine slows both
sides of a ratio. A test marked `timed` follows this rule whenever it is marked. A
scheduled run of `moxi-dev timed` measures at its machine's set factor: its tick sets the
variable to the factor `moxi-dev timed schedule --slack` set on that machine, 1 by default.

The idle gate. `MOXI_TEST_IDLE=<line>@<deadline>`, read from the environment pytest
starts with, gates every `timed` test the run runs: `<line>` is how much other work, in
CPUs, may run on the machine while a test measures (a number above 0), and `<deadline>`
is the run's last moment to wait for that, in Unix seconds. `moxi-dev timed` sets it;
unset, nothing is gated. Before a timed test's call phase (its fixtures already built)
the kit samples the gauge for `GATE_SAMPLE_S` (0.5 s). Under the line, the test runs.
Over it, the kit samples again every `GATE_RESAMPLE_S` (10 s) while the next sample would
start by the deadline; if none reads under the line, the test is "not measured" and
doesn't run. The deadline bounds only the waiting: a test past it still takes its one
sample, and never waits for another, so one deadline shared by every session of a run
spends its wait once. A test that starts within `GATE_CHAIN_S` (1 s) of the previous
timed test's end, when that test's window read under the line, takes no sample: its
window begins where that one ended. After the call phase the kit reads the gauge again.
A test's window runs from the start of the gate's last sample to the end of its call
phase, and a test whose window read at or over the line is "not measured" whatever it
read, a pass or a failure alike. Each timed test ends `passed`, `failed`, `not measured`
or `skipped` (its own skip, as pytest says). pytest reports "not measured" as a skip whose
reason starts `not measured:` and names the other work, the line and the wait; a failure
it voided keeps its text in `durations.json`, not among pytest's failures.

The gauge (`Gauge`). On Linux it reads the first line of `/proc/stat`, every CPU's time
in clock ticks (user, nice, system, irq, softirq and steal are busy; idle and iowait are
not), and this process's CPU time with its reaped children's, from `os.times`. Other
work over an interval is the busy ticks' rise less this process's CPU time in ticks,
divided by the interval's ticks per CPU. Where there is no `/proc/stat` (macOS) it falls
back to the one-minute load average, read when the gate samples and after the call, the
higher judged against the line in the load average's own units; it can miss a burst
shorter than about 20 s. Where neither exists (Windows) there is no gauge, and every timed
test the gate meets is "not measured: no gauge on this system".

A timed test reports what it measured through the `timed_reading` fixture, called once:
`timed_reading(value, budget, unit)`, with `clock="child"` when a child process measured
itself and the test read what it printed. A test that doesn't call it still records its
outcome.

Selection. Five variables, read from the environment pytest starts with, change what
runs; `moxi-dev check` sets them for each suite, and a contributor may set them by hand:

- `MOXI_TEST_TIERS=all` puts back every test of the `browser`, `slow` and `build` tiers
  that the package's `-m` deselected. A `live` test is never put back.
- `MOXI_TEST_CHANGED=<file>` names a file of repository paths that changed, separated by
  NUL bytes. A `browser`, `slow` or `build` test (never a `live` one) marked
  `@pytest.mark.touches("packages/<p>/<glob>")` is put back when one of its globs
  matches one of the paths (`touches_match`). A glob that does not start with
  `packages/` is refused when the tests are collected, since it could never match.
- `MOXI_TEST_FINDING=L<n>[,L<n>...]` runs only the tests marked `finding` with one of
  those arguments, a tier test among them put back as above; every other test is
  deselected.
- `MOXI_TEST_TIMED=hold` deselects every `timed` test the run would otherwise run, and
  the run's `durations.json` names them under `held` (under xdist the workers collect,
  and each hands its list to the controller). `MOXI_TEST_TIMED=only` deselects every
  test that is not `timed`. Both apply after the rest of the selection and the run's own
  `-m` and `-k`, so with the same other variables an `only` run runs exactly the tests a
  `hold` run held.
- `MOXI_TEST_LEAVE_OUT=<tier>[,<tier>...]` names tiers (of `browser`, `slow` and
  `build`) whose tests are never put back, whatever the variables above ask; the
  package's `-m` still deselects them. `moxi-dev check` sets `browser` for a package
  whose browser tests can't run because Playwright's Chromium can't start.

A test put back gets the marker `lifted`, and the run's `-m` expression becomes
`(<expression>) or lifted`, so pytest's own deselection keeps it and `-k` still applies.

Durations. Each run writes `durations.json`: every test's seconds by phase (`setup`,
`call`, `teardown`), its outcome and, for a test this run put back, `lifted` with its
tiers, from the reports the main process sees (under xdist, the controller's), and,
under `MOXI_TEST_TIMED=hold`, the `held` tests' ids. Under the idle gate each timed test
has a `timed` entry: `outcome`, `clock` (`wall` or `child`), `gauge` (`stat`, `loadavg`
or `none`), `other` (other work over its window, or at the gate's last sample when it
never ran), `line`, `reading`, `budget` and `unit` (from `timed_reading`, else null),
`failure` (a failure's text, kept when the gate voided it) and `waited_s`; and the file
has a `gate` entry: the gauge, the line, the deadline, the gate's first sample and the
seconds it waited in all. It goes to the path in
`MOXI_TEST_DURATIONS`, else to
`moxi-dev/durations.json` in pytest's cache directory; with neither (the cache plugin
off) nothing is written.

Stacks. With `MOXI_TEST_STACKS=<dir>` and `MOXI_TEST_STACKS_AT=<time>` (seconds since
the epoch) in the environment pytest starts with, each process of the run (under xdist
the controller and every worker) that is still running at that time writes every
thread's stack (`faulthandler`) to `<dir>/<worker or main>-<pid>.txt`, whatever ran
before in it. pytest's own faulthandler plugin cancels the one timer a process has on
each failed or errored test and on entering pdb, so the kit arms it again after those
and after each test; while a test runs under pytest's per-test faulthandler timeout that
setting holds the timer. Each process opens its file at the start, so one that ended in
time leaves it empty. `moxi-dev check` sets both for each package's run, shortly before
its bound (`arm_stacks`). It also sets `MOXI_TEST_PARENT_PID` to its own pid: the run's
first process watches that process (`watch_parent`) and, once it has ended by any death
(a SIGKILL, a TerminateProcess), ends every process under it, SIGTERM and then SIGKILL,
then on Linux and macOS its own process group, then itself.

A seam with two implementations: `Seam` parametrizes a fixture over every
implementation for the contract suites and gives the rest the default one.
"""

from __future__ import annotations

import contextlib
import dataclasses
import faulthandler
import functools
import inspect
import io
import ipaddress
import json
import math
import ntpath
import os
import random
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable, Generator, Iterable, Iterator, Mapping, Sequence
from enum import Enum
from pathlib import Path, PurePath
from typing import Any, TypeVar
from urllib.parse import parse_qs, urlsplit

import pytest
from moxi.paths import ANCHOR_HOME_ENV, HOME_ENV

from moxi_dev import temp
from moxi_dev.temp import FENCED_ENV as FENCED_ENV
from moxi_dev.temp import TEMP_ENV as TEMP_ENV

#: How often a served server looks for its shutdown request, in seconds.
SHUTDOWN_POLL = 0.05

#: Seconds a test's positive wait on a process may take before it fails: a process starts,
#: a binding appears, a row shows, a stream opens, a server is gone. It bounds a wait for
#: what must happen, so it is long: a passing wait returns as soon as its state holds. A
#: wait that checks something does not happen keeps its own short bound.
PROC_WAIT_S = 20.0

#: The tiers a package's `addopts` deselects, so a plain `pytest` runs units and seams.
TIERS = ("browser", "slow", "build", "live")

#: Every marker the kit registers, with the line `pytest --markers` shows.
MARKERS = {
    "browser": "drives a browser; deselected by a plain pytest run",
    "live": "reaches real services; deselected by a plain pytest run, and runs only "
            "with MOXI_TEST_LIVE=1",
    "slow": "takes long; deselected by a plain pytest run",
    "build": "builds and installs distributions end to end, past what every run can "
             "afford (minutes, uv's warm cache); a quick offline build stays in the "
             "plain run; deselected by a plain pytest run",
    "touches(*globs)": "the repository paths whose change puts this test back in "
                       "moxi-dev check",
    "finding(tag)": "the finding this test holds",
    "timed": "asserts a wall-clock budget; moxi-dev check holds it back, and "
             "moxi-dev timed runs it",
    "honest_fsync": "os.fsync is the real one in this test",
    "honest_ports": "the unit plan's listener check makes its real TCP connect in this "
                    "test",
}

#: The variable a `live` test needs, set to 1.
LIVE_ENV = "MOXI_TEST_LIVE"

#: `all`: every tier but `live` runs (module doc, Selection).
TIERS_ENV = "MOXI_TEST_TIERS"
#: A file of the repository paths that changed, NUL-separated (module doc, Selection).
CHANGED_ENV = "MOXI_TEST_CHANGED"
#: `L<n>[,L<n>...]`: only the tests that carry one of these findings run.
FINDING_ENV = "MOXI_TEST_FINDING"
#: Where `durations.json` goes.
DURATIONS_ENV = "MOXI_TEST_DURATIONS"
#: `hold` or `only`: what a run does with the `timed` tests (module doc, Selection).
TIMED_ENV = "MOXI_TEST_TIMED"
TIMED_MODES = ("hold", "only")
#: A factor of at least 1 for every upper bound of a `timed` test (module doc, Timed
#: tests).
TIMED_SLACK_ENV = "MOXI_TEST_TIMED_SLACK"
#: The descriptor, open on the run lock, that a scheduled tick hands the `moxi-dev timed`
#: run it starts, which the run locks again in place of opening its own (`moxi_dev.timed`).
TIMED_LOCK_FD_ENV = "MOXI_TEST_TIMED_LOCK_FD"
#: Tiers this run never puts back, comma-separated (module doc, Selection).
LEAVE_OUT_ENV = "MOXI_TEST_LEAVE_OUT"
#: The directory each process of a run writes its stacks to (module doc, Stacks).
STACKS_ENV = "MOXI_TEST_STACKS"
#: When, in seconds since the epoch, the processes write them (module doc, Stacks).
STACKS_AT_ENV = "MOXI_TEST_STACKS_AT"
#: The pid of the process whose end ends the run (module doc, Stacks).
PARENT_PID_ENV = "MOXI_TEST_PARENT_PID"
#: How often a run watching its parent looks whether it is still running.
PARENT_POLL_S = 1.0
#: How long the run's processes get after SIGTERM when its parent is gone, before SIGKILL.
PARENT_GRACE_S = 2.0

#: The marker of a test that asserts a wall-clock budget (module doc, Timed tests).
TIMED = "timed"
#: The key under which an xdist worker hands the controller the tests it held.
_HELD_KEY = "moxi_dev_held"

#: Where every `touches` glob starts: the changed paths are the repository's, and each
#: package is under it.
TOUCHES_ROOT = "packages/"

#: The marker the kit sets on a tier test it puts back; not for tests to use.
LIFTED = "lifted"

#: The shape of a finding's name, as `finding(...)` and `MOXI_TEST_FINDING` take it.
FINDING_NAME = re.compile(r"L[0-9]+")

#: The tiers a selection can put back: every tier but `live`.
LIFTABLE = tuple(tier for tier in TIERS if tier != "live")

#: The harness homes the fence points under the test's home, each relative to it.
HARNESS_HOMES = {
    "HOME": ".",
    "XDG_CONFIG_HOME": ".config",
    "XDG_DATA_HOME": ".local/share",
    "XDG_STATE_HOME": ".local/state",
    "XDG_CACHE_HOME": ".cache",
    "CLAUDE_CONFIG_DIR": ".claude",
    "CODEX_HOME": ".codex",
}

#: `1`: a proof row may drive the system's real supervisor (systemd, launchd, Task
#: Scheduler); the core's units fence refuses a real supervisor call under pytest without it.
REAL_SUPERVISOR_ENV = "MOXI_TEST_REAL_SUPERVISOR"

#: Variables carried into the fence unchanged, when set.
KEPT_ENV = ("PATH", "LANG", "LANGUAGE", "PYTEST_CURRENT_TEST", "PYTEST_VERSION",
            "PYTEST_XDIST_WORKER", "PYTEST_XDIST_WORKER_COUNT", "PYTEST_XDIST_TESTRUNUID",
            REAL_SUPERVISOR_ENV)

#: Whether this is Windows, where the fence also moves the user's folders and keeps the
#: system's variables; tests replace it.
WINDOWS = os.name == "nt"

#: On Windows, the user's folders the fence also points under the test's home, each
#: relative to it, laid out as Windows lays out a profile. The `WIN_PD_OVERRIDE_*`
#: variables are platformdirs' per-user folders. `HOMEDRIVE` and `HOMEPATH` are the home
#: split at its drive, and `TEMP` and `TMP` are the test's short directory.
WINDOWS_HOMES = {
    "USERPROFILE": ".",
    "LOCALAPPDATA": "AppData/Local",
    "APPDATA": "AppData/Roaming",
    "WIN_PD_OVERRIDE_LOCAL_APPDATA": "AppData/Local",
    "WIN_PD_OVERRIDE_APPDATA": "AppData/Roaming",
    "WIN_PD_OVERRIDE_USER_PROGRAM_FILES": "AppData/Local/Programs",
    "WIN_PD_OVERRIDE_PROGRAMS": "AppData/Roaming/Microsoft/Windows/Start Menu/Programs",
    "WIN_PD_OVERRIDE_TEMPLATES": "AppData/Roaming/Microsoft/Windows/Templates",
    "WIN_PD_OVERRIDE_PERSONAL": "Documents",
    "WIN_PD_OVERRIDE_DOWNLOADS": "Downloads",
    "WIN_PD_OVERRIDE_DESKTOPDIRECTORY": "Desktop",
    "WIN_PD_OVERRIDE_MYPICTURES": "Pictures",
    "WIN_PD_OVERRIDE_MYVIDEO": "Videos",
    "WIN_PD_OVERRIDE_MYMUSIC": "Music",
}

#: On Windows, the system's variables carried into the fence unchanged, when set: what a
#: process needs to start and load its libraries (`SYSTEMROOT` above all: sockets and
#: OpenSSL need it), `PATHEXT` to find a program by its bare name, `COMSPEC` for a
#: shell, `USERNAME`, the only place `getpass.getuser` finds the user's name there, and
#: `PROGRAMDATA`, the machine's shared settings folder, without which Windows' own
#: OpenSSH exits 255 at once. It is no user's folder, and Windows hands a program that
#: folder whatever the variable says, so clearing it moved nothing.
WINDOWS_KEPT_ENV = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "OS",
                    "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "USERNAME",
                    "PROGRAMDATA")

#: The child keys that name a folder uv writes, each under the run's roots (module doc,
#: The run).
UV_DIRS_ENV = ("UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR", "UV_PYTHON_BIN_DIR", "UV_TOOL_DIR",
               "UV_TOOL_BIN_DIR")
#: The interpreter uv runs, and whether it may download another.
UV_PYTHON_ENV = "UV_PYTHON"
UV_DOWNLOADS_ENV = "UV_PYTHON_DOWNLOADS"
#: Python's two byte-code variables: set non-empty, the first writes none; the second
#: names the folder byte code goes to instead of beside its source.
NO_BYTE_CODE_ENV = "PYTHONDONTWRITEBYTECODE"
PYCACHE_PREFIX_ENV = "PYTHONPYCACHEPREFIX"
#: Every child key, which the run's environment and each test's carry.
CHILD_ENV = (*UV_DIRS_ENV, UV_PYTHON_ENV, UV_DOWNLOADS_ENV, NO_BYTE_CODE_ENV)
#: Where Playwright finds its browsers, which the run's environment sets to the folder
#: the started environment finds.
BROWSERS_ENV = "PLAYWRIGHT_BROWSERS_PATH"

#: The run's folders in its pytest temp root: its homes, uv's folders but the cache, the
#: cache when the run has no xdist controller, and an isolated child's byte code.
RUN_HOME = "run-home"
RUN_UV = "run-uv"
UV_CACHE = "uv-cache"
PYCACHE = "pycache"

#: A path of either flavour, kept as the caller's.
_P = TypeVar("_P", bound=PurePath)

_KEPT_PREFIXES = ("LC_",)
_GROUP = "moxi.modules"
_MODULE_NAME = re.compile(r"[a-z0-9]+")
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
_REAL_FSYNC = os.fsync
_REAL_FDATASYNC = getattr(os, "fdatasync", None)


class FenceError(PermissionError):
    """A write outside the test's tmp, or a connect off this machine, the fence refused."""


@dataclasses.dataclass(frozen=True)
class Fence:
    """What the fence set up for one test."""

    #: `HOME`, under the test's `tmp_path`.
    home: Path
    #: `XDG_RUNTIME_DIR`, in the test's short directory.
    runtime: Path
    #: The test's short directory, removed after the test.
    short: Path
    #: The environment as the fence left it.
    env: Mapping[str, str]
    #: Where a write may land.
    roots: tuple[Path, ...]
    #: `MOXI_ANCHOR_HOME`: the test's stand-in for the account's own folder.
    anchor_home: Path

    def module_home(self, module: str) -> Path:
        """Where `MOXI_<MODULE>_HOME` points for `module`."""
        return _module_home(self.home, self.short, module)

    def profile_dir(self, name: str) -> Path:
        """A new directory `name` inside the test's own profile, its home (`USERPROFILE`
        on Windows), for a file a module keeps only inside the user's profile there, such
        as comms' token store. The test owns it; it goes with the test's tmp."""
        path = self.home / name
        path.mkdir(parents=True)
        return path


# --- the audit hook --------------------------------------------------------------------

class _Guard:
    """The hook's state, shared by every thread of the process."""

    def __init__(self) -> None:
        self.active = False
        self.network = True
        self.roots: tuple[str, ...] = ()
        self.short: str | None = None          # the current test's short directory
        self.breaches: list[str] = []
        self.reported = 0

    def save(self) -> tuple:
        return (self.active, self.network, self.roots, self.short, list(self.breaches),
                self.reported)

    def restore(self, saved: tuple) -> None:
        (self.active, self.network, self.roots, self.short, breaches,
         self.reported) = saved
        self.breaches[:] = breaches


_GUARD = _Guard()
_LOCAL = threading.local()
_INSTALLED = False
#: The directories `short_dir` has made and not yet removed, by their real paths.
_SHORT_DIRS: set[str] = set()


def _fd_path(dir_fd: object) -> str | None:
    if dir_fd is None or dir_fd == -1:
        return os.getcwd()
    try:
        return os.readlink(f"/proc/self/fd/{int(dir_fd)}")  # type: ignore[call-overload]
    except (OSError, ValueError, TypeError):
        return None


def _resolve(path: object, dir_fd: object = None, *, follow: bool = True) -> str | None:
    """The real path a write to `path` lands on, or None when it can't be told."""
    if path is None or isinstance(path, int):
        return None
    try:
        text = os.fsdecode(path)  # type: ignore[arg-type]
    except TypeError:
        return None
    if not text:
        return None
    if not os.path.isabs(text):
        base = _fd_path(dir_fd)
        if base is None:
            return None
        text = os.path.join(base, text)
    if follow:
        return os.path.realpath(text)
    head, tail = os.path.split(os.path.normpath(text))
    return os.path.join(os.path.realpath(head), tail)


def _null_device(real: str) -> bool:
    """Whether a resolved path is the null device (module doc, Writes)."""
    if not WINDOWS:
        return real == os.devnull
    path = real.replace("/", "\\").lower()
    if path == "\\\\.\\nul":
        return True
    # Past a \\?\ or \\.\ prefix Windows takes the name as it is: a file named nul.
    if path.startswith(("\\\\?\\", "\\\\.\\")):
        return False
    return ntpath.isabs(path) and ntpath.basename(path) == "nul"


def _plain(real: str) -> str:
    """On Windows, a long-form path (`\\\\?\\C:\\...`, `\\\\?\\UNC\\server\\share\\...`) as
    the plain path it names; any other path as it is.

    Past the prefix Windows takes each name literally, so a long form whose plain path
    normalizes to something else (a `..`, a `.`, a `/`, a trailing separator) names no
    plain path and is kept long, which no root matches."""
    if not WINDOWS or not real.startswith("\\\\?\\"):
        return real
    rest = real[4:]
    if rest[:4].upper() == "UNC\\":
        plain = "\\\\" + rest[4:]
    elif rest[:1].isalpha() and rest[1:3] == ":\\":
        plain = rest
    else:
        return real
    return plain if ntpath.normpath(plain) == plain else real


def _under(real: str, roots: Iterable[str]) -> bool:
    """Whether a resolved path is one of `roots` or inside one, each judged as the plain
    path it names (`_plain`)."""
    real = _plain(real)
    sep = ntpath.sep if WINDOWS else os.sep
    return any(real == root or real.startswith(root + sep)
               for root in map(_plain, roots))


def _allowed(real: str) -> bool:
    if _null_device(real) or real == "/dev/tty" or real.startswith("/dev/pts/"):
        return True
    return _under(real, (*_GUARD.roots, *_SHORT_DIRS))


def _write(event: str, path: object, dir_fd: object = None, *,
           follow: bool = True) -> str | None:
    real = _resolve(path, dir_fd, follow=follow)
    if real is None or _allowed(real):
        return None
    return f"{event} {real}: outside the test's tmp"


def _on_open(args: tuple) -> str | None:
    path, mode, flags = (tuple(args) + (None, None, None))[:3]
    writes = ((isinstance(mode, str) and any(c in mode for c in "wax+"))
              or (isinstance(flags, int) and bool(flags & _WRITE_FLAGS)))
    return _write("open", path) if writes else None


def _on_sqlite(args: tuple) -> str | None:
    database = args[0] if args else None
    if isinstance(database, bytes):
        database = os.fsdecode(database)
    if isinstance(database, PurePath):
        database = str(database)
    if not isinstance(database, str) or database in ("", ":memory:"):
        return None
    if database.startswith("file:"):
        parts = urlsplit(database)
        if parse_qs(parts.query).get("mode", [""])[-1] in ("ro", "memory"):
            return None
        database = parts.path
        if not database:
            return None
    return _write("sqlite3.connect", database)


def _loopback(host: object) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    host = host.split("%", 1)[0].lower()
    if host in ("", "localhost") or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.is_loopback or ip.is_unspecified:
        return True
    mapped = getattr(ip, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


def _unix_path(address: object) -> object:
    """A filesystem AF_UNIX address, or None (abstract or unnamed)."""
    if isinstance(address, (str, bytes)) and address and address[:1] not in ("\0", b"\0"):
        return address
    return None


def _on_reach(event: str) -> Callable[[tuple], str | None]:
    def check(args: tuple) -> str | None:
        sock, address = (tuple(args) + (None, None))[:2]
        family = getattr(sock, "family", None)
        if family == getattr(socket, "AF_UNIX", object()):
            path = _unix_path(address)
            return None if path is None or not _GUARD.network else _write(event, path)
        if family in (socket.AF_INET, socket.AF_INET6) and _GUARD.network:
            if isinstance(address, tuple) and address and not _loopback(address[0]):
                return f"{event} {address[0]}:{address[1] if len(address) > 1 else ''}: " \
                       "not a loopback address"
        return None
    return check


def _on_bind(args: tuple) -> str | None:
    sock, address = (tuple(args) + (None, None))[:2]
    if getattr(sock, "family", None) != getattr(socket, "AF_UNIX", object()):
        return None
    path = _unix_path(address)
    return None if path is None else _write("socket.bind", path, follow=False)


def _arg(index: int, event: str, *, dir_fd: int | None = None,
         follow: bool = True) -> Callable[[tuple], str | None]:
    def check(args: tuple) -> str | None:
        if len(args) <= index:
            return None
        fd = args[dir_fd] if dir_fd is not None and len(args) > dir_fd else None
        return _write(event, args[index], fd, follow=follow)
    return check


def _both(event: str) -> Callable[[tuple], str | None]:
    first = _arg(0, event, dir_fd=2, follow=False)
    second = _arg(1, event, dir_fd=3, follow=False)
    return lambda args: first(args) or second(args)


_HANDLERS: dict[str, Callable[[tuple], str | None]] = {
    "open": _on_open,
    "os.mkdir": _arg(0, "os.mkdir", dir_fd=2, follow=False),
    "os.rename": _both("os.rename"),
    "os.remove": _arg(0, "os.remove", dir_fd=1, follow=False),
    "os.rmdir": _arg(0, "os.rmdir", dir_fd=1, follow=False),
    "os.symlink": _arg(1, "os.symlink", dir_fd=2, follow=False),
    "os.link": _arg(1, "os.link", dir_fd=3, follow=False),
    "os.truncate": _arg(0, "os.truncate"),
    "os.chmod": _arg(0, "os.chmod", dir_fd=2),
    "os.chown": _arg(0, "os.chown", dir_fd=3),
    "os.utime": _arg(0, "os.utime", dir_fd=3),
    "os.setxattr": _arg(0, "os.setxattr"),
    "os.removexattr": _arg(0, "os.removexattr"),
    "shutil.rmtree": _arg(0, "shutil.rmtree", dir_fd=1, follow=False),
    "shutil.copyfile": _arg(1, "shutil.copyfile"),
    "shutil.copytree": _arg(1, "shutil.copytree"),
    "shutil.move": _arg(1, "shutil.move", follow=False),
    "sqlite3.connect": _on_sqlite,
    "tempfile.mkstemp": _arg(0, "tempfile.mkstemp"),
    "tempfile.mkdtemp": _arg(0, "tempfile.mkdtemp"),
    "socket.bind": _on_bind,
    "socket.connect": _on_reach("socket.connect"),
    "socket.sendto": _on_reach("socket.sendto"),
    "socket.sendmsg": _on_reach("socket.sendmsg"),
}


def _on_start(env_at: int) -> Callable[[tuple], str | None]:
    """A start by `os.exec*`, `os.spawn*` or `os.posix_spawn*`: its environment, at
    `env_at` in the event's arguments, is judged as the seam judges one (`_refusal`), and
    must also hold every child key. None inherits this process's."""
    def check(args: tuple) -> str | None:
        env = args[env_at] if len(args) > env_at else None
        if env is None:
            return None
        try:
            given = {os.fsdecode(key): os.fsdecode(value) for key, value in env.items()}
        except (AttributeError, TypeError):
            return None
        lacks = [key for key in _RUN.child if not _holds(given, key)]
        if lacks:
            return f"a start whose environment lacks {', '.join(lacks)}"
        return _refusal(given)
    return check


#: The starts the hook judges for the whole loop, not only a test's span.
_START_HANDLERS: dict[str, Callable[[tuple], str | None]] = {
    "os.exec": _on_start(2),
    "os.spawn": _on_start(3),
    "os.posix_spawn": _on_start(2),
}


def _audit(event: str, args: tuple) -> None:
    handler = _START_HANDLERS.get(event) if _RUN.depth else None
    if handler is None:
        if not _GUARD.active:
            return
        handler = _HANDLERS.get(event)
    if handler is None or getattr(_LOCAL, "busy", False):
        return
    _LOCAL.busy = True
    try:
        refusal = handler(args)
    finally:
        _LOCAL.busy = False
    if refusal:
        _refuse(refusal)


def _refuse(refusal: str) -> None:
    """Record a refusal (while a test is fenced) and raise it."""
    if _GUARD.active:
        _GUARD.breaches.append(refusal)
    raise FenceError(f"the test fence refused {refusal}")


def _install() -> None:
    global _INSTALLED
    if not _INSTALLED:
        sys.addaudithook(_audit)
        _INSTALLED = True


# --- the fixture -----------------------------------------------------------------------

_SESSION: dict[str, Any] = {}


#: This process's short root outside a fence, under `"root"`: its short process dir, or
#: in a nested run's child process the outer test's short directory.
_SHORT: dict[str, str] = {}


def short_root() -> str:
    """Where short directories go: inside the current test's short directory while a
    test is fenced, else inside the run's short directory while the test loop runs
    (module doc, The run), else this process's short process dir (module doc, Temp
    files), else the system temp directory."""
    if _GUARD.active and _GUARD.short:
        return _GUARD.short
    return _RUN.short or _SHORT.get("root") or tempfile.gettempdir()


#: The characters of a test's short directory's one-character name.
_SHORT_NAME_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789"

#: The one-character names this process's fence has still to give, in a shuffled turn.
_SHORT_NAMES: list[str] = []


def _make_test_short_dir(root: str) -> Path:
    """Make a test's short directory, or the run's, private, in `root`: `short_root()`,
    which is the run's short directory for a test's, and the process dir (or in a nested
    run the outer test's short directory) for the run's.

    Its name is one character, so a fenced test's short path is two bytes longer than a
    short dir `mx-` and 8 characters made straight in the root: on macOS
    `/tmp/mx-<6>/<1>/<1>`, 18 bytes, and a module's sockets under it keep within macOS's
    104-byte limit. The
    names are given in shuffled turns of 36: no name repeats within a turn, and each
    turn is a fresh shuffle, so a name may come back as soon as the next turn starts.
    A name that exists is always passed over, so the directory is always new. When every
    one-character name exists, the name is `tempfile`'s own."""
    tried: list[str] = []
    while len(tried) < len(_SHORT_NAME_CHARS):
        if not _SHORT_NAMES:
            _SHORT_NAMES.extend(random.SystemRandom().sample(_SHORT_NAME_CHARS,
                                                             len(_SHORT_NAME_CHARS)))
        name = _SHORT_NAMES.pop()
        if name in tried:
            continue
        tried.append(name)
        path = os.path.join(root, name)
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            continue
        return Path(path)
    return Path(tempfile.mkdtemp(prefix="", dir=root))


def _modules() -> list[str]:
    """The installed Moxi modules, by their `moxi.modules` entry points."""
    from importlib import metadata

    found = {ep.name for ep in metadata.entry_points(group=_GROUP)}
    return sorted(name for name in found if _MODULE_NAME.fullmatch(name))


def _spelled_long(path: _P) -> _P:
    """On Windows, `path` in its long form: its real path, each part spelled in full
    (`C:\\Users\\RUNNER~1` reads `C:\\Users\\runneradmin`; noqa: MX6 -- GitHub's
    Windows runner profile). Elsewhere `path` as it is."""
    return type(path)(ntpath.realpath(path)) if WINDOWS else path


def _module_home(home: _P, short: _P, module: str) -> _P:
    """Where the fence points `MOXI_<MODULE>_HOME` for `module`: in the short directory,
    or on Windows under the test's `LOCALAPPDATA`, inside its profile."""
    if WINDOWS:
        return home / WINDOWS_HOMES["LOCALAPPDATA"] / "moxi" / module
    return short / module


def _fenced_env(source: Mapping[str, str], home: Path, runtime: Path, short: Path,
                modules: Iterable[str]) -> dict[str, str]:
    home, runtime, short = (_spelled_long(path) for path in (home, runtime, short))
    env = {key: value for key, value in source.items()
           if key in KEPT_ENV or key.startswith(_KEPT_PREFIXES)
           or (WINDOWS and key.upper() in WINDOWS_KEPT_ENV)}
    for key, rel in HARNESS_HOMES.items():
        env[key] = str(home if rel == "." else home / rel)
    env["XDG_RUNTIME_DIR"] = str(runtime)
    for module in modules:
        env[f"MOXI_{module.upper()}_HOME"] = str(_module_home(home, short, module))
    if WINDOWS:
        for key, rel in WINDOWS_HOMES.items():
            env[key] = str(home if rel == "." else home / rel)
        env["HOMEDRIVE"], env["HOMEPATH"] = ntpath.splitdrive(str(home))
        env["TEMP"] = env["TMP"] = str(short)
    env[ANCHOR_HOME_ENV] = env["LOCALAPPDATA"] if WINDOWS else str(home)
    return env


# --- the run ---------------------------------------------------------------------------

class _Run:
    """The run's state in this process (module doc, The run)."""

    def __init__(self) -> None:
        #: How many test loops run in this process: 1 in the loop that owns the run, more
        #: in a loop that borrows it.
        self.depth = 0
        #: The environment pytest started with; inside a borrowing loop, the outer test's
        #: when that loop started.
        self.started: dict[str, str] | None = None
        #: The child keys and their values.
        self.child: dict[str, str] = {}
        #: What every test's environment carries from the run's: the child keys and
        #: Playwright's browsers folder.
        self.carried: dict[str, str] = {}
        #: The run's roots, by their real paths: its pytest temp root, its short
        #: directory and its uv cache.
        self.roots: tuple[str, ...] = ()
        #: The run's short directory, which holds every short directory the loop makes.
        self.short: str | None = None
        #: Where an isolated Python child puts its byte code.
        self.pycache: str | None = None
        #: Whether the run opted into the real supervisor, where a child's homes and temp
        #: directory may be the account's own.
        self.real_supervisor = False


_RUN = _Run()

#: The key a controller hands its xdist workers the run's uv cache under.
_UV_CACHE_KEY = "moxi_dev_uv_cache"

#: The temp directory's variables a child is given when it lacks them: `tempfile` reads
#: `TMPDIR`, then `TEMP` and `TMP`, on every system.
_TEMP_KEYS = ("TMPDIR", "TEMP", "TMP")

#: A module's home variable, which the fence moves.
_MODULE_HOME = re.compile(r"MOXI_[A-Z0-9]+_HOME")

#: `subprocess.Popen`'s own `__init__`, which the seam calls on.
_POPEN_INIT = subprocess.Popen.__init__
_POPEN_SIGNATURE = inspect.signature(_POPEN_INIT)


def started_env() -> dict[str, str]:
    """The environment pytest started with, before the run's or any test's fence: a
    copy. Inside a pytest run inside a test (pytester's in-process run), the outer
    test's environment when it started that run. Before the test loop (at collection)
    it is this process's environment, which is still the started one."""
    return dict(_RUN.started if _RUN.started is not None else os.environ)


def _name(key: str) -> str:
    """A variable's name as this system compares it: on Windows, in upper case."""
    return key.upper() if WINDOWS else key


def _value(env: Mapping[str, str], key: str) -> str | None:
    """`key`'s value in `env`, on Windows in whatever case `env` spells it; None when
    unset."""
    value = env.get(key)
    if value is not None or not WINDOWS:
        return value
    wanted = key.upper()
    for name, value in env.items():
        if name.upper() == wanted:
            return value
    return None


def _holds(env: Mapping[str, str], key: str) -> bool:
    return _value(env, key) is not None


def _put(env: dict[str, str], key: str, value: str) -> None:
    """Set `key` in `env`, dropping any other spelling of it on Windows."""
    for name in [name for name in env if _name(name) == _name(key)]:
        del env[name]
    env[key] = value


def _moved(key: str) -> bool:
    """Whether the fence moves the variable `key`: a home, the runtime or temp directory,
    `MOXI_HOME` or a module's home."""
    name = _name(key)
    return (name in HARNESS_HOMES or name in _TEMP_KEYS
            or name in ("XDG_RUNTIME_DIR", HOME_ENV) or bool(_MODULE_HOME.fullmatch(name))
            or (WINDOWS and name in WINDOWS_HOMES))


def _in_roots(path: str) -> bool:
    """Whether `path`, resolved, is one of the run's roots or inside one."""
    roots = (*_RUN.roots, *_GUARD.roots, *_SHORT_DIRS)
    real = os.path.realpath(path)
    if WINDOWS:
        return _under(ntpath.normcase(real), [ntpath.normcase(root) for root in roots])
    return _under(real, roots)


def _lax() -> bool:
    """Whether a child's homes and temp directory may name the account's own: a run that
    opted into the real supervisor, or a `live` test."""
    return _RUN.real_supervisor or (_GUARD.active and not _GUARD.network)


def _refusal(env: Mapping[str, str]) -> str | None:
    """Why the fence refuses a child `env`, or None: a uv folder outside the run's roots,
    byte code on with no prefix under them, or, outside the opt-in tiers, a variable the
    fence moves set to a path that exists outside them (one that names nothing on disk,
    such as `/nowhere`, passes)."""
    for key in UV_DIRS_ENV:
        value = _value(env, key)
        if value and not _in_roots(value):
            return f"a child's {key}={value}: outside the run's roots"
    if not _value(env, NO_BYTE_CODE_ENV):
        prefix = _value(env, PYCACHE_PREFIX_ENV)
        if not (prefix and _in_roots(prefix)):
            return (f"a child with byte code on and no {PYCACHE_PREFIX_ENV} under the "
                    "run's roots")
    if not _lax():
        for key, value in env.items():
            if value and _moved(key) and os.path.exists(value) and not _in_roots(value):
                return f"a child's {key}={value}: outside the run's roots"
    return None


def _child_env(given: Mapping[Any, Any]) -> dict[str, str]:
    """The environment the seam hands a child given `given`: each missing child key
    added, a missing temp directory and home added, then judged (`_refusal`)."""
    env = {os.fsdecode(key): os.fsdecode(value) for key, value in given.items()}
    # This process's environment: the run's, or the test's inside a fence.
    current_env = dict(os.environ)
    for key, value in _RUN.child.items():
        current = _value(env, key)
        # An empty uv variable reads as unset to uv; an empty byte-code one turns it on.
        if current is None or (not current and key != NO_BYTE_CODE_ENV):
            _put(env, key, value)
    for key in _TEMP_KEYS if WINDOWS else _TEMP_KEYS[:1]:
        if not _holds(env, key) and current_env.get(key):
            env[key] = current_env[key]
    if WINDOWS:
        own = _value(env, "USERPROFILE") or _value(env, "HOME")
        for key, rel in WINDOWS_HOMES.items():
            if not _holds(env, key):
                value = ((own if rel == "." else ntpath.join(own, *rel.split("/")))
                         if own else current_env.get(key))
                if value:
                    env[key] = value
    if not _holds(env, "HOME") and current_env.get("HOME"):
        env["HOME"] = current_env["HOME"]
    refusal = _refusal(env)
    if refusal:
        _refuse(refusal)
    return env


def _is_python(program: str) -> bool:
    """Whether `program` names a Python interpreter: the suite's own, or a file whose name
    starts with `python`."""
    if os.path.basename(program).lower().startswith("python"):
        return True
    suite = _suite_python()
    return bool(suite) and os.path.realpath(program) == suite


@functools.cache
def _suite_python() -> str:
    """The suite's interpreter, resolved once; empty when Python can't tell it."""
    return os.path.realpath(sys.executable) if sys.executable else ""


def _with_prefix(argv: Any) -> list[Any] | None:
    """`argv` with `-X pycache_prefix=<the run's byte-code folder>` after the interpreter,
    when it starts a Python interpreter isolated (`-I` or `-E` among its options) and
    gives neither `-B` nor a prefix of its own; else None. A prefix of its own outside
    the run's roots is refused."""
    if isinstance(argv, (str, bytes)) or not _RUN.pycache:
        return None
    try:
        items = list(argv)
        words = [os.fsdecode(item) for item in items]
    except TypeError:
        return None
    if not words or not _is_python(words[0]):
        return None
    isolated = no_byte_code = False
    prefix = None
    at = 1
    while at < len(words):
        word = words[at]
        if word in ("-X", "-W", "--check-hash-based-pycs"):
            option = words[at + 1] if at + 1 < len(words) else ""
            if word == "-X" and option.startswith("pycache_prefix="):
                prefix = option.partition("=")[2]
            at += 2
            continue
        if word.startswith("-X"):
            if word[2:].startswith("pycache_prefix="):
                prefix = word.partition("=")[2]
            at += 1
            continue
        if word.startswith("-W"):
            at += 1
            continue
        if word in ("-", "--") or not word.startswith("-") or word.startswith("--"):
            break
        flags = word[1:]
        for flag in flags:
            if flag in "cm":
                break
            isolated = isolated or flag in "IE"
            no_byte_code = no_byte_code or flag == "B"
        if "c" in flags or "m" in flags:
            break
        at += 1
    if prefix is not None:
        if prefix and not _in_roots(prefix):
            _refuse(f"a child's -X pycache_prefix={prefix}: outside the run's roots")
        return None
    if not isolated or no_byte_code:
        return None
    return [items[0], "-X", f"pycache_prefix={_RUN.pycache}", *items[1:]]


@functools.wraps(_POPEN_INIT)
def _seam_init(self: subprocess.Popen, *args: Any, **kwargs: Any) -> None:
    """The seam over `subprocess.Popen`, in place for the whole test loop (module doc,
    The run): a child given an environment gets what it lacks and is refused what points
    outside; an isolated Python child gets a byte-code prefix under the run's roots."""
    if _RUN.depth:
        bound = _POPEN_SIGNATURE.bind(self, *args, **kwargs)
        given = bound.arguments
        if given.get("env") is not None:
            given["env"] = _child_env(given["env"])
        if not given.get("shell"):
            argv = _with_prefix(given.get("args"))
            if argv is not None:
                given["args"] = argv
        args, kwargs = bound.args[1:], bound.kwargs
    _POPEN_INIT(self, *args, **kwargs)


def _browsers_dir(env: Mapping[str, str]) -> str:
    """The folder Playwright finds its browsers in under `env`: its own variable, else
    its per-system default under the home."""
    given = env.get(BROWSERS_ENV)
    if given:
        return given
    home = env.get("HOME") or os.path.expanduser("~")
    if WINDOWS:
        base = env.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    elif sys.platform == "darwin":
        base = os.path.join(home, "Library", "Caches")
    else:
        base = env.get("XDG_CACHE_HOME") or os.path.join(home, ".cache")
    return os.path.join(base, "ms-playwright")


def _uv_cache(basetemp: Path) -> Path:
    """The run's one uv cache in `basetemp`, made."""
    cache = basetemp / UV_CACHE
    cache.mkdir(exist_ok=True)
    return cache


def _put_env(env: Mapping[str, str]) -> None:
    """Make this process's environment exactly `env`."""
    os.environ.clear()
    os.environ.update(env)


@contextlib.contextmanager
def _owned(config: pytest.Config) -> Iterator[None]:
    """The run, for the loop that owns it: the run's environment in place, the seam on,
    and at the end the started environment back and the run's short directory gone."""
    factory = getattr(config, "_tmp_path_factory", None)
    if factory is None:                 # pytest's tmp_path plugin is off: no run
        yield
        return
    basetemp = _spelled_long(Path(os.path.realpath(factory.getbasetemp())))
    started = dict(os.environ)
    tempdir = tempfile.tempdir
    short = _spelled_long(_make_test_short_dir(short_root()))
    try:
        home = basetemp / RUN_HOME
        home.mkdir(exist_ok=True)
        home = _spelled_long(home)
        runtime = short / "run"
        runtime.mkdir(mode=0o700)
        workerinput = getattr(config, "workerinput", None) or {}
        cache = Path(workerinput.get(_UV_CACHE_KEY) or _uv_cache(basetemp))
        uv_dir, pycache = basetemp / RUN_UV, basetemp / PYCACHE
        pycache.mkdir(exist_ok=True)
        child = {"UV_CACHE_DIR": str(cache),
                 "UV_PYTHON_INSTALL_DIR": str(uv_dir / "python"),
                 "UV_PYTHON_BIN_DIR": str(uv_dir / "python-bin"),
                 "UV_TOOL_DIR": str(uv_dir / "tools"),
                 "UV_TOOL_BIN_DIR": str(uv_dir / "tool-bin"),
                 UV_PYTHON_ENV: sys.executable, UV_DOWNLOADS_ENV: "never",
                 NO_BYTE_CODE_ENV: "1"}
        env = _fenced_env(started, home, runtime, short, _SESSION.get("modules") or _modules())
        env["TMPDIR"] = env[FENCED_ENV] = str(short)
        _RUN.carried = {**child, BROWSERS_ENV: _browsers_dir(started)}
        env.update(_RUN.carried)
        _RUN.started, _RUN.child, _RUN.short = started, child, str(short)
        _RUN.roots = tuple(dict.fromkeys(os.path.realpath(path)
                                         for path in (basetemp, short, cache)))
        _RUN.pycache = str(pycache)
        _RUN.real_supervisor = started.get(REAL_SUPERVISOR_ENV) == "1"
        _put_env(env)
        tempfile.tempdir = str(short)
        subprocess.Popen.__init__ = _seam_init          # type: ignore[method-assign]
        _RUN.depth = 1
        yield
    finally:
        _RUN.depth = 0
        subprocess.Popen.__init__ = _POPEN_INIT         # type: ignore[method-assign]
        _put_env(started)
        tempfile.tempdir = tempdir
        _RUN.__init__()
        shutil.rmtree(short, ignore_errors=True)


@contextlib.contextmanager
def _borrowed() -> Iterator[None]:
    """The run, for a loop inside the loop that owns it (pytester's in-process run): the
    kit's state saved and put back at the end; nothing made and nothing removed."""
    saved = (_GUARD.save(), _RUN.started, dict(os.environ), tempfile.tempdir)
    _RUN.started = dict(os.environ)
    _RUN.depth += 1
    try:
        yield
    finally:
        _RUN.depth -= 1
        guard, _RUN.started, env, tempfile.tempdir = saved
        _GUARD.restore(guard)
        if dict(os.environ) != env:
            _put_env(env)


@pytest.hookimpl(wrapper=True)
def pytest_runtestloop(session: pytest.Session) -> Iterator[None]:
    """Put the run in place for the test loop (module doc, The run). xdist's controller
    runs no test, so it puts nothing in place."""
    if session.config.pluginmanager.has_plugin("dsession"):
        return (yield)
    with _borrowed() if _RUN.depth else _owned(session.config):
        return (yield)


def _quiet_fsync(fd: Any) -> None:
    """The fsync seam's stand-in: refuses a closed descriptor, syncs nothing."""
    os.fstat(fd if isinstance(fd, int) else fd.fileno())


def _nothing_answers(address: str, port: int, timeout: float = 0.5) -> bool:
    """The port seam's stand-in: no connect, and nothing listens anywhere."""
    return False


def _quiet_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Put the port seam's stand-in in place of the unit plan's connect, where the core
    installed has one."""
    try:
        from moxi.units import _plan
    except ImportError:
        return
    if hasattr(_plan, "answers"):
        monkeypatch.setattr(_plan, "answers", _nothing_answers)


def pytest_configure(config: pytest.Config) -> None:
    for name, line in MARKERS.items():
        config.addinivalue_line("markers", f"{name}: {line}")
    config.addinivalue_line("markers", f"{LIFTED}: set by the kit on a tier test this "
                                       "run puts back; not for tests to use")
    config.stash[_TEMP_RUN] = run = _TempRun.start(config)
    config.pluginmanager.register(run, "moxi-dev-temp")
    # Per run, from the environment this pytest started with; a pytest run inside a
    # fenced test sees the fenced one, so the outer run's selection never reaches it.
    config.stash[_SELECTION] = selection(os.environ)
    config.pluginmanager.register(_TimedSplit(), "moxi-dev-timed")
    asked = idle(os.environ)
    if asked is not None:
        config.stash[_GATE] = gate = _Gate(config, asked)
        config.pluginmanager.register(gate, "moxi-dev-gate")
    if not hasattr(config, "workerinput"):          # an xdist worker reports to the main
        config.pluginmanager.register(_Durations(config), "moxi-dev-durations")
    # Read once, from the environment this process started with: a pytest run inside a
    # test (pytester's in-process run) already sees the fenced one.
    _SESSION.setdefault("live", os.environ.get(LIVE_ENV) == "1")
    _SESSION.setdefault("timed_slack", _timed_slack(os.environ))
    _SESSION.setdefault("modules", _modules())
    arm_stacks(os.environ)
    if not hasattr(config, "workerinput"):
        watch_parent(os.environ)
    _install()


# This process's armed dump (`arm_stacks`): its open file and the time it is due.
_STACKS: dict[str, Any] = {}


def arm_stacks(env: Mapping[str, str]) -> Path | None:
    """Have this process write every thread's stack at the time `env` names (module doc,
    Stacks), into a file of its own; the file, or None when `env` names no directory or
    time, the time has passed, the file can't be made, or this process is already armed."""
    where = env.get(STACKS_ENV, "").strip()
    try:
        at = float(env.get(STACKS_AT_ENV, ""))
    except ValueError:
        return None
    if not where or _STACKS or not math.isfinite(at) or at - time.time() <= 0:
        return None
    name = env.get("PYTEST_XDIST_WORKER") or "main"
    path = Path(where) / f"{name}-{os.getpid()}.txt"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Held open, not closed by a `with`: faulthandler writes to it at the time.
        fh = open(path, "w", encoding="utf-8")
    except OSError:
        return None
    _STACKS.update(fh=fh, at=at)
    _rearm_stacks()
    return path


def _rearm_stacks() -> None:
    """Arm this process's dump again for the time left, since faulthandler keeps one
    timer a process and pytest's own faulthandler plugin cancels it on a failed or
    errored test, on entering pdb and around each test under its per-test timeout.
    Past the time, a file still empty gets the stacks now: the process is still running."""
    if not _STACKS:
        return
    fh, left_s = _STACKS["fh"], _STACKS["at"] - time.time()
    try:
        if left_s > 0:
            faulthandler.dump_traceback_later(left_s, file=fh)
        elif os.fstat(fh.fileno()).st_size == 0:
            faulthandler.dump_traceback(file=fh)
    except (OSError, ValueError):                   # the file was closed under us
        pass


@pytest.hookimpl(trylast=True)
def pytest_exception_interact() -> None:
    _rearm_stacks()


@pytest.hookimpl(trylast=True)
def pytest_enter_pdb() -> None:
    _rearm_stacks()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol() -> Generator[None, object, object]:
    try:
        return (yield)
    finally:
        _rearm_stacks()


# The thread watching this run's parent, once one is (`watch_parent`).
_WATCHER: list[threading.Thread] = []


def watch_parent(env: Mapping[str, str]) -> threading.Thread | None:
    """Have this run end, with every process under it, once the process `env` names in
    `MOXI_TEST_PARENT_PID` has ended (module doc, Stacks): the watching thread, or None
    when `env` names none or this process already watches. A parent already gone ends
    the run at once."""
    from moxi import proc

    try:
        pid = int(env.get(PARENT_PID_ENV, ""))
    except ValueError:
        return None
    if _WATCHER:
        return None
    parent = proc.identity(pid)

    def watch() -> None:
        while parent is not None and proc.alive(parent):
            time.sleep(PARENT_POLL_S)
        _end_this_run(pid)

    thread = threading.Thread(target=watch, name="moxi-dev-parent", daemon=True)
    _WATCHER.append(thread)
    thread.start()
    return thread


def _end_this_run(parent_pid: int) -> None:
    """End every process under this one, SIGTERM (TerminateProcess on Windows) and
    `PARENT_GRACE_S` later SIGKILL; then, on Linux and macOS, this process's group when it
    leads one, which takes the processes that left the tree but not the group; then this
    process."""
    import psutil

    with contextlib.suppress(OSError, ValueError):
        print(f"moxi-dev: the process that started this run ({parent_pid}) is gone, so "
              f"the run ends with every process under it", file=sys.__stderr__, flush=True)
    try:
        under = psutil.Process().children(recursive=True)
    except psutil.Error:
        under = []
    for one in under:
        with contextlib.suppress(psutil.Error):
            one.terminate()
    _, left = psutil.wait_procs(under, timeout=PARENT_GRACE_S)
    for one in left:
        with contextlib.suppress(psutil.Error):
            one.kill()
    if not WINDOWS and os.getpgrp() == os.getpid():
        import signal

        with contextlib.suppress(OSError):
            os.killpg(os.getpid(), signal.SIGKILL)
    os._exit(1)


def _timed_slack(env: Mapping[str, str]) -> float:
    """The factor `env` sets for timed bounds: 1.0 when unset; a value that is not a
    finite number of at least 1 stops the run."""
    raw = env.get(TIMED_SLACK_ENV, "").strip()
    if not raw:
        return 1.0
    try:
        factor = float(raw)
    except ValueError:
        factor = math.nan
    if not math.isfinite(factor) or factor < 1:
        raise pytest.UsageError(f"{TIMED_SLACK_ENV} is a finite number of at least 1, or "
                                f"unset; it is {raw!r}")
    return factor


def timed_slack() -> float:
    """What every upper bound on a wall-clock measurement in a `timed` test is multiplied
    by: `MOXI_TEST_TIMED_SLACK` as this run started with it, 1.0 when unset (module doc,
    Timed tests)."""
    return _SESSION.get("timed_slack", 1.0)


def pytest_runtest_setup(item: pytest.Item) -> None:
    if item.get_closest_marker("live") and not _SESSION.get("live"):
        pytest.skip(f"a live test runs only with {LIVE_ENV}=1")


# --- selection -------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Selection:
    """What a run puts back past the package's `-m`, and what it keeps (module doc)."""

    all_tiers: bool = False
    changed: tuple[str, ...] = ()
    findings: frozenset[str] = frozenset()
    timed: str = ""                     # "", "hold" or "only"
    left_out: frozenset[str] = frozenset()   # tiers never put back

    def lifts(self, item: pytest.Item) -> bool:
        """Whether this run puts `item` back: a `browser`, `slow` or `build` test, never
        a `live` one or one of a tier left out, that every tier, a changed path or a
        finding asks for."""
        if any(item.get_closest_marker(tier) for tier in ("live", *self.left_out)):
            return False
        if not any(item.get_closest_marker(tier) for tier in LIFTABLE):
            return False
        if self.all_tiers or (self.findings and findings_of(item) & self.findings):
            return True
        return any(touches_match(glob, path)
                   for mark in item.iter_markers("touches") for glob in mark.args
                   if isinstance(glob, str) for path in self.changed)


_SELECTION = pytest.StashKey[Selection]()


def selection(env: Mapping[str, str]) -> Selection:
    """The selection the environment asks for; `pytest.UsageError` for a bad value."""
    tiers = env.get(TIERS_ENV, "").strip()
    if tiers not in ("", "all"):
        raise pytest.UsageError(f"{TIERS_ENV} is 'all' or unset; it is {tiers!r}")
    changed: tuple[str, ...] = ()
    where = env.get(CHANGED_ENV, "")
    if where:
        try:
            text = Path(where).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise pytest.UsageError(f"{CHANGED_ENV}: can't read {where} ({exc})") from exc
        changed = tuple(path for path in text.split("\0") if path)
    names = [name.strip() for name in env.get(FINDING_ENV, "").split(",")]
    names = [name for name in names if name]
    bad = [name for name in names if not FINDING_NAME.fullmatch(name)]
    if bad:
        raise pytest.UsageError(f"{FINDING_ENV} names findings as L<n>, comma-separated; "
                                f"{', '.join(map(repr, bad))} is not one")
    timed = env.get(TIMED_ENV, "").strip()
    if timed not in ("", *TIMED_MODES):
        raise pytest.UsageError(f"{TIMED_ENV} is {' or '.join(map(repr, TIMED_MODES))} or "
                                f"unset; it is {timed!r}")
    left_out = [tier.strip() for tier in env.get(LEAVE_OUT_ENV, "").split(",")]
    left_out = [tier for tier in left_out if tier]
    bad = [tier for tier in left_out if tier not in LIFTABLE]
    if bad:
        raise pytest.UsageError(f"{LEAVE_OUT_ENV} names tiers of {', '.join(LIFTABLE)}, "
                                f"comma-separated; {', '.join(map(repr, bad))} is not one")
    return Selection(all_tiers=tiers == "all", changed=changed, findings=frozenset(names),
                     timed=timed, left_out=frozenset(left_out))


def findings_of(item: pytest.Item) -> set[str]:
    """The findings `item`'s `finding` marks name."""
    return {arg for mark in item.iter_markers("finding") for arg in mark.args
            if isinstance(arg, str)}


@functools.lru_cache(maxsize=256)
def _glob_regex(glob: str) -> re.Pattern[str]:
    out, i, n = [], 0, len(glob)
    while i < n:
        if glob.startswith("**/", i):
            out.append("(?:[^/]*/)*")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        elif glob[i] == "[" and (end := glob.find("]", i + 2)) != -1:
            body = glob[i + 1:end]
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append("[" + body.replace("\\", "\\\\") + "]")
            i = end + 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("".join(out))


def touches_match(glob: str, path: str) -> bool:
    """Whether a repository path matches a `touches` glob, both relative to the
    repository's root with `/` between parts. `*` and `?` stay within one part, `[...]`
    is a set of characters, and `**` is any number of parts (`a/**` is everything
    under `a`). A glob that matches a directory the path is under matches the path."""
    glob = glob.strip().strip("/")
    if not glob:
        return False
    regex = _glob_regex(glob)
    parts = path.strip("/").split("/")
    return any(regex.fullmatch("/".join(parts[:end])) for end in range(len(parts), 0, -1))


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Keep only a finding's tests when one is asked for, and put back the tier tests the
    run asks for, before pytest's own `-m` deselects (module doc, Selection)."""
    chosen = config.stash.get(_SELECTION, None)
    if chosen is None:
        return
    bad = [f"{item.nodeid}: {glob!r}" for item in items
           for mark in item.iter_markers("touches") for glob in mark.args
           if not (isinstance(glob, str)
                   and glob.strip().strip("/").startswith(TOUCHES_ROOT))]
    if bad:
        # A glob relative to the package would never match a changed path, and its test
        # would quietly run only under every tier.
        raise pytest.UsageError(
            f"a touches glob is a path from the repository's root, under {TOUCHES_ROOT} "
            f"(such as '{TOUCHES_ROOT}<package>/src/**'); these are not: " + "; ".join(bad))
    if chosen.findings:
        keep = [item for item in items if findings_of(item) & chosen.findings]
        dropped = [item for item in items if not findings_of(item) & chosen.findings]
        if dropped:
            config.hook.pytest_deselected(items=dropped)
            items[:] = keep
    lifted = False
    for item in items:
        if chosen.lifts(item):
            item.add_marker(getattr(pytest.mark, LIFTED))
            lifted = True
    if lifted and config.option.markexpr:
        config.option.markexpr = f"({config.option.markexpr}) or {LIFTED}"


_HELD = pytest.StashKey[list[str]]()


class _TimedSplit:
    """Holds the `timed` tests back, or keeps only them (module doc, Selection)."""

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, config: pytest.Config,
                                      items: list[pytest.Item]) -> None:
        # trylast: after the kit's own selection and after pytest's -m and -k, so what is
        # held is what this run would otherwise have run.
        chosen = config.stash.get(_SELECTION, None)
        if chosen is None or not chosen.timed:
            return
        timed = [item for item in items if item.get_closest_marker(TIMED)]
        rest = [item for item in items if not item.get_closest_marker(TIMED)]
        keep, dropped = (rest, timed) if chosen.timed == "hold" else (timed, rest)
        if dropped:
            config.hook.pytest_deselected(items=dropped)
            items[:] = keep
        if chosen.timed == "hold":
            held = [item.nodeid for item in timed]
            config.stash[_HELD] = held
            if hasattr(config, "workeroutput"):
                config.workeroutput[_HELD_KEY] = held


# --- durations -------------------------------------------------------------------------

class _Durations:
    """Collects each test's seconds by phase and writes `durations.json` at the end."""

    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self.tests: dict[str, dict[str, Any]] = {}
        self.held: set[str] = set()

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node: Any, error: Any) -> None:
        """An xdist worker is done: keep the tests it held back."""
        output = getattr(node, "workeroutput", None) or {}
        self.held.update(output.get(_HELD_KEY) or ())

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        entry = self.tests.setdefault(report.nodeid, {})
        entry[report.when] = round(entry.get(report.when, 0.0) + report.duration, 6)
        if LIFTED in report.keywords:
            entry["lifted"] = [tier for tier in LIFTABLE if tier in report.keywords]
        if report.failed:
            entry["outcome"] = "failed"
        elif report.skipped and entry.get("outcome") != "failed":
            entry["outcome"] = "skipped"
        else:
            entry.setdefault("outcome", "passed")
        timed = getattr(report, _TIMED_REPORT, None)
        if timed is not None:
            entry["timed"] = timed
        elif report.when == "teardown" and report.failed and "timed" in entry:
            # A teardown that failed fails the test, whatever its window read.
            entry["timed"] = {**entry["timed"], "outcome": "failed",
                              "failure": entry["timed"]["failure"] or report.longreprtext}

    def path(self) -> Path | None:
        given = os.environ.get(DURATIONS_ENV)
        if given:
            return Path(given)
        cache = getattr(self.config, "cache", None)
        return cache.mkdir("moxi-dev") / "durations.json" if cache is not None else None

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        path = self.path()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        body: dict[str, Any] = {"format": 1, "tests": self.tests}
        chosen = self.config.stash.get(_SELECTION, None)
        if chosen is not None and chosen.timed == "hold":
            body["held"] = sorted(self.held | set(self.config.stash.get(_HELD, [])))
        gate = self.config.stash.get(_GATE, None)
        if gate is not None:
            body["gate"] = gate.summary()
        run = self.config.stash.get(_TEMP_RUN, None)
        if run is not None:
            body["temp"] = run.choice.as_dict()
        part.write_text(json.dumps(body, indent=1, sort_keys=True) + "\n",
                        encoding="utf-8")
        os.replace(part, path)


# --- temp files ------------------------------------------------------------------------

#: The key a controller hands its temp choice to its xdist workers under.
_TEMP_KEY = "moxi_dev_temp"
_TMP_PATH = pytest.StashKey[Path]()
_FAILED = pytest.StashKey[bool]()


def _workers(config: pytest.Config) -> int:
    """The run's xdist worker count, 1 without workers."""
    count = getattr(config.option, "numprocesses", None)
    if isinstance(count, int) and count > 0:
        return count
    if count in ("auto", "logical"):
        return os.cpu_count() or 1
    return 1


class _TempRun:
    """Where one pytest process keeps its temp, and its cleanup (module doc, Temp
    files)."""

    def __init__(self, config: pytest.Config, choice: temp.Choice,
                 swept: temp.Swept | None) -> None:
        self.config = config
        self.choice = choice
        self.swept = swept
        self.process_dir: temp.Held | None = None
        self.basetemp: temp.Held | None = None

    @classmethod
    def start(cls, config: pytest.Config) -> _TempRun:
        """Choose (a top-level controller), take the controller's choice (a worker) or
        leave it all to the outer run (a nested run); then make the process dir."""
        workerinput = getattr(config, "workerinput", None)
        swept = None
        if workerinput is not None:
            data = workerinput.get(_TEMP_KEY)
            choice = (temp.Choice.from_dict(data) if data
                      else temp.nested(tempfile.gettempdir()))
        elif not temp.top_level(os.environ):
            outer = _GUARD.short if _GUARD.active and _GUARD.short \
                else temp.outer_short_dir(os.environ)
            choice = temp.nested(outer or tempfile.gettempdir())
        else:
            basetemp = config.option.basetemp
            if not basetemp:
                swept = temp.sweep(own_root=temp.given_root(os.environ))
            try:
                choice = temp.choose(os.environ, basetemp=str(basetemp) if basetemp else None,
                                     workers=_workers(config))
            except temp.TempError as exc:
                raise pytest.UsageError(str(exc)) from exc
            temp.put_root(choice, os.environ)
        run = cls(config, choice, swept)
        if not choice.is_nested:
            run.process_dir = temp.make_process_dir(choice.short_root)
            _SHORT["root"] = str(run.process_dir.path)
        elif not _GUARD.active:
            _SHORT["root"] = choice.short_root
        return run

    @pytest.hookimpl(optionalhook=True)
    def pytest_configure_node(self, node: Any) -> None:
        """An xdist worker starts: hand it the whole choice, and the run's one uv cache,
        which this controller makes in its own temp root (module doc, The run)."""
        node.workerinput[_TEMP_KEY] = self.choice.as_dict()
        factory = getattr(self.config, "_tmp_path_factory", None)
        if factory is not None:
            basetemp = _spelled_long(Path(os.path.realpath(factory.getbasetemp())))
            node.workerinput[_UV_CACHE_KEY] = str(_uv_cache(basetemp))

    @pytest.hookimpl(tryfirst=True)
    def pytest_sessionstart(self, session: pytest.Session) -> None:
        """Record the basetemp (a worker's own, under xdist) when it is in memory."""
        factory = getattr(self.config, "_tmp_path_factory", None)
        if not self.choice.is_in_memory or factory is None:
            return
        try:
            self.basetemp = temp.hold(factory.getbasetemp())
        except temp.TempError as exc:
            raise pytest.UsageError(str(exc)) from exc

    def pytest_report_header(self, config: pytest.Config) -> list[str]:
        lines = [f"moxi-dev temp: {self.choice.header()}"]
        removed = self.swept.line() if self.swept is not None else None
        if removed:
            lines.append(f"moxi-dev temp: {removed}")
        return lines

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_setup(self, item: pytest.Item) -> Iterator[None]:
        try:
            return (yield)
        finally:
            path = (getattr(item, "funcargs", None) or {}).get("tmp_path")
            if self.choice.is_in_memory and isinstance(path, Path):
                item.stash[_TMP_PATH] = path

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_makereport(self, item: pytest.Item,
                                  call: pytest.CallInfo[None]) -> Iterator[None]:
        """In memory, remove a test's `tmp_path` once its teardown ends, unless a phase
        of it failed."""
        report = yield
        if report.failed:
            item.stash[_FAILED] = True
        if call.when == "teardown" and not item.stash.get(_FAILED, False):
            path = item.stash.get(_TMP_PATH, None)
            if path is not None:
                shutil.rmtree(path, ignore_errors=True)
        return report

    @pytest.hookimpl(trylast=True)
    def pytest_unconfigure(self, config: pytest.Config) -> None:
        """The run ends: the controller removes a basetemp in memory, then its record; a
        worker lets go of its record; each process removes its process dir."""
        if self.basetemp is not None:
            is_controller = not hasattr(config, "workerinput")
            self.basetemp.release(remove=is_controller)
            if is_controller:
                _drop_user_dir(self.basetemp.path)
            self.basetemp = None
        if self.process_dir is not None:
            self.process_dir.release()
            self.process_dir = None
            _SHORT.pop("root", None)


_TEMP_RUN = pytest.StashKey[_TempRun]()


def _drop_user_dir(basetemp: Path) -> None:
    """After a removed basetemp: pytest's `<prefix>current` link to it, and the
    `pytest-of-<user>` directory it was in once that is empty."""
    user_dir = basetemp.parent
    with contextlib.suppress(OSError):
        for link in user_dir.iterdir():
            if link.is_symlink() and link.name.endswith("current") \
                    and os.path.basename(os.readlink(link)) == basetemp.name:
                link.unlink()
        user_dir.rmdir()


# --- the idle gate ---------------------------------------------------------------------

#: `<line>@<deadline>`: how much other work, in CPUs, may run while a `timed` test
#: measures, and the run's last moment to wait for that, in Unix seconds (module doc,
#: The idle gate).
IDLE_ENV = "MOXI_TEST_IDLE"

#: The kernel's count of every CPU's time, in clock ticks, which the Linux gauge reads.
PROC_STAT = "/proc/stat"

#: How long the gate samples the gauge before a timed test, in seconds.
GATE_SAMPLE_S = 0.5
#: How often the gate samples again while other work is over the line, in seconds.
GATE_RESAMPLE_S = 10.0
#: How soon after the previous timed test's end a test must start to carry on its
#: window without a sample of its own, in seconds.
GATE_CHAIN_S = 1.0

#: The gauges, as the record names them: the kernel's CPU counters, the one-minute load
#: average, or none on this system.
GAUGES = ("stat", "loadavg", "none")

#: The clocks `timed_reading` takes: the test's own wall clock, or a child process that
#: measured itself and printed its reading.
TIMED_CLOCKS = ("wall", "child")

#: The outcome of a timed test whose result the gate didn't count.
NOT_MEASURED = "not measured"

#: The attribute a report carries the test's `timed` entry in, which xdist hands on.
_TIMED_REPORT = "moxi_dev_timed"
#: The key under which an xdist worker hands the controller its gate's figures.
_GATE_KEY = "moxi_dev_gate"
#: `/proc/stat`'s busy fields, after the line's `cpu` (guest time is inside user's).
_BUSY_FIELDS = (0, 1, 2, 5, 6, 7)          # user, nice, system, irq, softirq, steal
_IDLE_FIELDS = (3, 4)                      # idle, iowait
_PER_CPU = re.compile(r"cpu[0-9]+")


@dataclasses.dataclass(frozen=True)
class Idle:
    """What `MOXI_TEST_IDLE` asks of the gate."""

    #: Other work, in CPUs (the load average's own units on its gauge), a test's window
    #: must stay under.
    line: float
    #: The run's last moment to wait for the line, in Unix seconds.
    deadline: float


def idle(env: Mapping[str, str]) -> Idle | None:
    """The gate `env` asks for, None when it asks for none; `pytest.UsageError` for a bad
    value."""
    raw = env.get(IDLE_ENV, "").strip()
    if not raw:
        return None
    line_text, sep, deadline_text = raw.partition("@")
    try:
        line, deadline = float(line_text), float(deadline_text)
    except ValueError:
        line = deadline = math.nan
    if not (sep and math.isfinite(line) and line > 0 and math.isfinite(deadline)):
        raise pytest.UsageError(
            f"{IDLE_ENV} is <line>@<deadline>: other work in CPUs, above 0, and the last "
            f"moment to wait, in Unix seconds; it is {raw!r}")
    return Idle(line=line, deadline=deadline)


# The gauge's sources, one function each so a test can put a stand-in machine in their
# place.

def _read_proc_stat() -> str | None:
    """`/proc/stat`'s text, None where it can't be read."""
    try:
        with open(PROC_STAT, encoding="ascii") as fh:
            return fh.read()
    except OSError:
        return None


def _load_average() -> float | None:
    """The one-minute load average, None where the system has none."""
    try:
        return os.getloadavg()[0]
    except (AttributeError, OSError):
        return None


def _own_cpu_s() -> float:
    """This process's CPU time and its reaped children's, in seconds."""
    spent = os.times()
    return spent.user + spent.system + spent.children_user + spent.children_system


def _ticks_per_s() -> int:
    """How many clock ticks `/proc/stat` counts in a second."""
    try:
        return os.sysconf("SC_CLK_TCK")
    except (AttributeError, ValueError, OSError):
        return 100


def _now() -> float:
    """Now, in Unix seconds."""
    return time.time()


def _pause(seconds: float) -> None:
    """Wait `seconds`."""
    time.sleep(seconds)


def stat_counts(text: str) -> tuple[int, int, int]:
    """From `/proc/stat`'s text: the busy ticks and every tick over all CPUs, and how many
    CPUs it lists. Busy is user, nice, system, irq, softirq and steal; idle and iowait
    are not. `ValueError` when it has no `cpu` line."""
    busy = total = cpus = 0
    found = False
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        if fields[0] == "cpu":
            values = [int(value) for value in fields[1:9]]
            values += [0] * (8 - len(values))
            busy = sum(values[i] for i in _BUSY_FIELDS)
            total = busy + sum(values[i] for i in _IDLE_FIELDS)
            found = True
        elif _PER_CPU.fullmatch(fields[0]):
            cpus += 1
    if not found:
        raise ValueError(f"{PROC_STAT} has no line for all CPUs")
    return busy, total, cpus or os.cpu_count() or 1


@dataclasses.dataclass(frozen=True)
class GaugeReading:
    """One reading of the gauge."""

    #: When it was taken, in Unix seconds.
    at: float
    #: `stat`: the busy ticks and every tick over all CPUs, and the CPU count.
    busy: int = 0
    total: int = 0
    cpus: int = 1
    #: `stat`: this process's CPU time and its reaped children's, in seconds.
    own_s: float = 0.0
    #: `loadavg`: the one-minute load average.
    load: float = 0.0


@dataclasses.dataclass(frozen=True)
class Gauge:
    """How much other work runs on this machine (module doc, The idle gate)."""

    #: One of `GAUGES`.
    kind: str

    @classmethod
    def find(cls) -> Gauge:
        """The gauge this system has: `stat`, else `loadavg`, else `none`."""
        if _read_proc_stat() is not None:
            return cls("stat")
        if _load_average() is not None:
            return cls("loadavg")
        return cls("none")

    def read(self) -> GaugeReading:
        """A reading now."""
        at = _now()
        if self.kind == "stat":
            busy, total, cpus = stat_counts(_read_proc_stat() or "")
            return GaugeReading(at=at, busy=busy, total=total, cpus=cpus,
                                own_s=_own_cpu_s())
        if self.kind == "loadavg":
            return GaugeReading(at=at, load=_load_average() or 0.0)
        return GaugeReading(at=at)

    def other(self, start: GaugeReading, end: GaugeReading) -> float:
        """Other work between two readings. `stat`: the busy ticks' rise less this
        process's CPU time in ticks, divided by the interval's ticks per CPU, in CPUs.
        `loadavg`: the higher of the two load averages."""
        if self.kind == "loadavg":
            return max(start.load, end.load)
        if self.kind != "stat":
            return 0.0
        total = end.total - start.total
        if total <= 0:
            return 0.0
        own = (end.own_s - start.own_s) * _ticks_per_s()
        return max(0.0, end.busy - start.busy - own) / (total / end.cpus)

    def sample(self) -> tuple[GaugeReading, float]:
        """One sample before a test: its first reading, and other work over it. On
        `stat` it lasts `GATE_SAMPLE_S`; the load average is read once."""
        start = self.read()
        if self.kind != "stat":
            return start, self.other(start, start)
        _pause(GATE_SAMPLE_S)
        return start, self.other(start, self.read())

    def describe(self, other: float) -> str:
        """Other work in words: CPUs, or the load average in its own units."""
        if self.kind == "loadavg":
            return f"load average {other:.2f}"
        return f"other work {other:.2f} CPUs"


@dataclasses.dataclass
class _Window:
    """A timed test's window: where it starts, and what the gate waited for it."""

    start: GaugeReading
    waited_s: float


_WINDOW = pytest.StashKey[_Window]()
#: Why the gate kept a test from running, and other work at its last sample (None with
#: no gauge).
_KEPT_OUT = pytest.StashKey[tuple[str, float | None, float]]()
_READING = pytest.StashKey[dict[str, Any]]()
_GATE = pytest.StashKey["_Gate"]()


class _Gate:
    """The idle gate around every `timed` test (module doc, The idle gate)."""

    def __init__(self, config: pytest.Config, asked: Idle) -> None:
        self.config = config
        self.line = asked.line
        self.deadline = asked.deadline
        self.gauge = Gauge.find()
        #: The end of the previous timed test's window, when it read under the line.
        self.last: GaugeReading | None = None
        self.first_sample: float | None = None
        self.waited_s = 0.0

    def summary(self) -> dict[str, Any]:
        """The gate's figures for `durations.json`."""
        return {"gauge": self.gauge.kind, "line": self.line, "deadline": self.deadline,
                "first_sample": _rounded(self.first_sample),
                "waited_s": round(self.waited_s, 3)}

    def _keep_out(self, item: pytest.Item, reason: str, other: float | None,
                  waited_s: float) -> None:
        item.stash[_KEPT_OUT] = (reason, other, waited_s)
        pytest.skip(reason)

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_call(self, item: pytest.Item) -> None:
        """Before the test's call phase: carry on the previous window, or sample until
        other work is under the line, or keep the test out."""
        if not item.get_closest_marker(TIMED):
            return
        if self.gauge.kind == "none":
            self._keep_out(item, f"{NOT_MEASURED}: no gauge on this system", None, 0.0)
        last, self.last = self.last, None
        if last is not None and _now() - last.at <= GATE_CHAIN_S:
            item.stash[_WINDOW] = _Window(start=last, waited_s=0.0)
            return
        start, other = self.gauge.sample()
        sampled = _now()
        if self.first_sample is None:
            self.first_sample = other
        # The deadline bounds the waiting only: a test past it still takes its one
        # sample, and never another.
        while other >= self.line and start.at + GATE_RESAMPLE_S <= self.deadline:
            _pause(max(0.0, start.at + GATE_RESAMPLE_S - _now()))
            start, other = self.gauge.sample()
        waited = max(0.0, _now() - sampled)
        self.waited_s += waited
        if other >= self.line:
            self._keep_out(item, f"{NOT_MEASURED}: machine busy, "
                                 f"{self.gauge.describe(other)} at or over the line "
                                 f"{self.line:g}, waited {waited:.0f} s", other, waited)
        item.stash[_WINDOW] = _Window(start=start, waited_s=waited)

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_makereport(self, item: pytest.Item,
                                  call: pytest.CallInfo[None]
                                  ) -> Generator[None, pytest.TestReport, pytest.TestReport]:
        """After the call phase: read the gauge, judge the window, and put the test's
        `timed` entry on its report."""
        if not item.get_closest_marker(TIMED) or call.when == "teardown":
            return (yield)
        window = item.stash.get(_WINDOW, None) if call.when == "call" else None
        end = self.gauge.read() if window is not None else None
        report = yield
        if call.when == "setup" and report.passed:
            return report
        entry: dict[str, Any] = {
            "outcome": report.outcome, "clock": "wall", "gauge": self.gauge.kind,
            "other": None, "line": self.line, "reading": None, "budget": None,
            "unit": None, "failure": report.longreprtext if report.failed else None,
            "waited_s": 0.0}
        entry.update(item.stash.get(_READING, {}))
        kept_out = item.stash.get(_KEPT_OUT, None) if call.when == "call" else None
        if kept_out is not None:
            _, other, waited = kept_out
            entry.update(outcome=NOT_MEASURED, other=_rounded(other),
                         waited_s=round(waited, 3))
        elif window is not None and end is not None:
            other = self.gauge.other(window.start, end)
            under = other < self.line
            entry.update(other=_rounded(other), waited_s=round(window.waited_s, 3))
            self.last = end if under else None
            if not under and (report.passed or report.failed) \
                    and not hasattr(report, "wasxfail"):
                entry["outcome"] = NOT_MEASURED
                where, line = item.reportinfo()[:2]
                reason = (f"{NOT_MEASURED}: machine busy, {self.gauge.describe(other)} "
                          f"over the test's window, at or over the line {self.line:g}, "
                          f"waited {window.waited_s:.0f} s")
                report.outcome = "skipped"
                # The shape pytest gives a skip: where, and the reason after `Skipped: `.
                report.longrepr = (os.fspath(where), (line or 0) + 1, f"Skipped: {reason}")
        setattr(report, _TIMED_REPORT, entry)
        return report

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        output = getattr(self.config, "workeroutput", None)
        if output is not None:                  # an xdist worker hands its figures on
            output[_GATE_KEY] = {"first_sample": self.first_sample,
                                 "waited_s": self.waited_s}

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node: Any, error: Any) -> None:
        """An xdist worker is done: add its waits to the run's."""
        figures = (getattr(node, "workeroutput", None) or {}).get(_GATE_KEY) or {}
        if self.first_sample is None:
            self.first_sample = figures.get("first_sample")
        self.waited_s += figures.get("waited_s") or 0.0


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


@pytest.fixture
def timed_reading(request: pytest.FixtureRequest) -> Callable[..., None]:
    """The reporter a `timed` test calls once with what it measured (module doc, The
    idle gate): `timed_reading(value, budget, unit, *, clock="wall")`.

    `value` and `budget` are finite numbers in `unit` (`"ms"`, `"s"`, `"us"`, a ratio's
    `"x"`); `clock` is `"child"` when a child process measured itself and the test read
    what it printed. Under the gate the kit writes them into the test's `timed` entry in
    `durations.json`. A test not marked `timed` that asks for it fails at setup, and a
    second call raises `ValueError`."""
    item = request.node
    if not item.get_closest_marker(TIMED):
        pytest.fail("timed_reading is for a test marked timed", pytrace=False)

    def report(value: float, budget: float, unit: str, *, clock: str = "wall") -> None:
        if _READING in item.stash:
            raise ValueError("a timed test reports its reading once")
        for name, number in (("value", value), ("budget", budget)):
            if isinstance(number, bool) or not isinstance(number, (int, float)) \
                    or not math.isfinite(number):
                raise ValueError(f"timed_reading's {name} is a finite number; it is "
                                 f"{number!r}")
        if not isinstance(unit, str) or not unit.strip():
            raise ValueError(f"timed_reading's unit names the unit; it is {unit!r}")
        if clock not in TIMED_CLOCKS:
            raise ValueError(f"timed_reading's clock is one of {', '.join(TIMED_CLOCKS)}; "
                             f"it is {clock!r}")
        item.stash[_READING] = {"reading": float(value), "budget": float(budget),
                                "unit": unit, "clock": clock}

    return report


# --- a seam with two implementations -------------------------------------------------------

class Seam:
    """A seam with two or more implementations, behind one fixture.

    The contract suites (`contract`, test file names such as `test_delivery.py`) run
    each of their tests once on every implementation; every other test that asks for
    the fixture gets `default`. In the package's `conftest.py`:

        BACKEND = Seam("backend", ["mock", "local"], default="mock",
                       contract=["test_delivery.py", "test_claims.py"])
        backend = BACKEND.fixture

        def pytest_generate_tests(metafunc):
            BACKEND.parametrize(metafunc)

    The fixture's value is the implementation (a name, usually), which the package's own
    fixtures build from. Test ids are `param_id` of each implementation, the same on
    every run. A test that parametrizes the fixture itself is left as it is."""

    def __init__(self, name: str, implementations: Iterable[Any], *, default: Any,
                 contract: Iterable[str]) -> None:
        values = tuple(implementations)
        if len(values) < 2:
            raise ValueError(f"seam {name!r}: a seam has two implementations or more")
        if len(set(map(param_id, values))) != len(values):
            raise ValueError(f"seam {name!r}: two implementations have the same id")
        if default not in values:
            raise ValueError(f"seam {name!r}: the default {default!r} is not one of "
                             f"its implementations")
        self.name = name
        self.implementations = values
        self.default = default
        self.contract = frozenset(contract)

        @pytest.fixture(name=name)
        def _fixture(request: pytest.FixtureRequest) -> Any:
            return getattr(request, "param", default)

        self.fixture = _fixture

    def is_contract(self, metafunc: pytest.Metafunc) -> bool:
        """Whether `metafunc`'s test is in one of the contract suites."""
        module = getattr(metafunc, "module", None)
        where = getattr(module, "__file__", None)
        return where is not None and Path(where).name in self.contract

    def parametrize(self, metafunc: pytest.Metafunc) -> None:
        """Call from `pytest_generate_tests`: a contract test that asks for the fixture
        runs on each implementation."""
        if self.name not in metafunc.fixturenames or not self.is_contract(metafunc):
            return
        for mark in metafunc.definition.iter_markers("parametrize"):
            names = mark.args[0] if mark.args else mark.kwargs.get("argnames", ())
            if isinstance(names, str):
                names = [part.strip() for part in names.split(",")]
            if self.name in names:
                return
        metafunc.parametrize(self.name, self.implementations, indirect=True, ids=param_id)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Iterator[None]:
    start = len(_GUARD.breaches)
    try:
        result = yield
    except BaseException:
        _GUARD.reported = len(_GUARD.breaches)
        raise
    new = _GUARD.breaches[start:]
    if new:
        _GUARD.reported = len(_GUARD.breaches)
        pytest.fail("the test fence refused, and the test went on:\n  "
                    + "\n  ".join(new), pytrace=False)
    return result


@pytest.fixture(autouse=True)
def fence(request: pytest.FixtureRequest, tmp_path: Path,
          tmp_path_factory: pytest.TempPathFactory,
          monkeypatch: pytest.MonkeyPatch) -> Iterator[Fence]:
    """Fence the test in: its environment, its writes and its connects (module doc)."""
    _install()
    # A fence set up inside a fenced test (pytester's in-process run) puts the outer
    # one back when it ends; the outermost leaves the hook idle.
    outer = _GUARD.save()
    basetemp = tmp_path_factory.getbasetemp()
    short = _spelled_long(_make_test_short_dir(short_root()))
    home = tmp_path / "home"
    runtime = short / "run"
    home.mkdir()
    home = _spelled_long(home)
    runtime.mkdir(mode=0o700)
    env = _fenced_env(os.environ, home, runtime, short,
                      _SESSION.get("modules") or _modules())
    env["TMPDIR"] = env[FENCED_ENV] = str(short)
    env.update(_RUN.carried)
    for key in [k for k in os.environ if k not in env]:
        monkeypatch.delenv(key)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(tempfile, "tempdir", str(short))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    if not request.node.get_closest_marker("honest_fsync"):
        monkeypatch.setattr(os, "fsync", _quiet_fsync)
        if _REAL_FDATASYNC is not None:
            monkeypatch.setattr(os, "fdatasync", _quiet_fsync)
    if not (request.node.get_closest_marker("honest_ports")
            or request.node.get_closest_marker("live")):
        _quiet_ports(monkeypatch)
    roots: list[str] = []
    for root in (basetemp, short):
        real = os.path.realpath(root)
        if real not in roots:
            roots.append(real)
    _GUARD.roots = tuple(roots)
    _GUARD.short = os.path.realpath(short)
    _GUARD.network = not request.node.get_closest_marker("live")
    _GUARD.reported = len(_GUARD.breaches)
    _GUARD.active = True
    try:
        yield Fence(home=home, runtime=runtime, short=short, env=dict(env),
                    roots=tuple(Path(r) for r in roots), anchor_home=Path(env[ANCHOR_HOME_ENV]))
    finally:
        late = _GUARD.breaches[_GUARD.reported:]
        _GUARD.restore(outer)
        shutil.rmtree(short, ignore_errors=True)
    if late:
        pytest.fail("the test fence refused, outside the test's call:\n  "
                    + "\n  ".join(late), pytrace=False)


@pytest.fixture
def uv_env(fence: Fence) -> dict[str, str]:
    """An environment for a child process that runs uv: the fenced one, a copy, which
    holds the child keys (module doc, The run): uv's folders under the run's roots, the
    run's one uv cache, the suite's Python and no Python downloads."""
    return {**os.environ, **_RUN.child}


# --- the terminal record ---------------------------------------------------------------

#: The record's format the fixture writes: the core's own (`moxi.terminal.FORMAT`).
RECORD_FORMAT = 1


@pytest.fixture
def terminal_record(fence: Fence) -> Callable[..., Path]:
    """A writer of the core's record of the terminal's environment, under the test's
    anchor (`MOXI_ANCHOR_HOME`), for a test whose code reads it, such as a page that
    checks before it writes.

    `terminal_record(env=None, *, version=None)` writes the record of `env` (this
    process's environment as the fence left it, without one) through the core's own
    writer (`moxi.terminal.write`): each variable of the core set in it, with its exact
    value, and nothing else. `version` is the writing core's version, this core's
    without one. It returns the record's path.
    """
    def write(env: Mapping[str, str] | None = None, *, version: str | None = None) -> Path:
        from moxi import terminal

        return terminal.write(env, moxi=version)

    return write


# --- helpers ---------------------------------------------------------------------------

def wait_for(predicate: Callable[[], Any], timeout: float = 5.0, *, interval: float = 0.01,
             what: str | Callable[[], str] = "the condition") -> Any:
    """Call `predicate` until it returns something true and return that; fail with
    `AssertionError` naming `what` after `timeout` seconds. A positive wait on a process
    passes `timeout=PROC_WAIT_S`."""
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out after {timeout:g}s waiting for "
                                 f"{what() if callable(what) else what}")
        time.sleep(interval)


@contextlib.contextmanager
def short_dir(prefix: str = "mx-") -> Iterator[Path]:
    """A new directory with a short path (under /tmp where it can be), removed after.

    A Unix socket's path is limited to about 100 bytes; pytest's tmp_path is often
    longer than that. In a fenced test it is made inside the test's short directory;
    made elsewhere (by a fixture of wider scope), it is one of the fence's allowed roots
    until it is removed, so a test may reach a server whose socket is in it."""
    path = Path(tempfile.mkdtemp(prefix=prefix, dir=short_root()))
    real = os.path.realpath(path)
    _SHORT_DIRS.add(real)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
        _SHORT_DIRS.discard(real)


#: Why a test that needs a symlink skips where `can_symlink()` is false.
NO_SYMLINKS = ("this account may not make symlinks (on Windows: it lacks the symlink "
               "privilege and Developer Mode is off)")

#: Windows' "a required privilege is not held", the error for a symlink made without it.
_ERROR_PRIVILEGE_NOT_HELD = 1314


@functools.cache
def can_symlink() -> bool:
    """Whether this account may make a symlink: tried once, on a real link in a new temp
    directory that is removed after, and the answer kept for the process.

    Only Windows' "a required privilege is not held" reads as no. Any other error is
    raised, `FenceError` among them, so a fence refusal is never taken for no."""
    with tempfile.TemporaryDirectory(prefix="mx-link-") as temp_dir:
        try:
            os.symlink(os.path.join(temp_dir, "target"), os.path.join(temp_dir, "link"))
        except OSError as exc:
            if getattr(exc, "winerror", None) != _ERROR_PRIVILEGE_NOT_HELD:
                raise
            return False
    return True


def need_symlinks() -> None:
    """Skip the test from here on where this account may not make a symlink."""
    if not can_symlink():
        pytest.skip(NO_SYMLINKS)


@pytest.fixture
def needs_symlinks() -> None:
    """For a test that makes a symlink: skipped where this account may not make one."""
    need_symlinks()


@contextlib.contextmanager
def served(make: Callable[[Path], Any], *, timeout: float = 5.0) -> Iterator[Any]:
    """Serve a `socketserver`-style server on a thread for the `with` block.

    `make(socket_path)` builds the server; the path is `sock` in a fresh `short_dir`,
    and a TCP server simply ignores it. The server is served with
    `serve_forever(poll_interval=SHUTDOWN_POLL)`, so leaving the block shuts it down
    within that poll; then `server_close()` runs (when it has one) and the directory
    goes."""
    with short_dir("mxs-") as where:
        server = make(where / "sock")
        started = threading.Event()

        def run() -> None:
            started.set()
            server.serve_forever(poll_interval=SHUTDOWN_POLL)

        thread = threading.Thread(target=run, daemon=True,
                                  name=f"served-{type(server).__name__}")
        thread.start()
        try:
            wait_for(started.is_set, timeout, what=f"{type(server).__name__} to start")
            yield server
        finally:
            if thread.is_alive():
                server.shutdown()
            thread.join(timeout)
            close = getattr(server, "server_close", None)
            if close is not None:
                close()


def param_id(value: object) -> str:
    """A test id for a parameter that is the same on every run: never a memory address.

    Use it as `ids=param_id` in `pytest.mark.parametrize` or `pytest.fixture(params=...)`.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return str(value)
    if isinstance(value, str):
        return value or "empty"
    if isinstance(value, bytes):
        return value.decode("ascii", "backslashreplace") or "empty"
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, PurePath):
        return value.name or str(value)
    if isinstance(value, (tuple, list)):
        return "-".join(param_id(v) for v in value) or "empty"
    name = getattr(value, "__qualname__", None) or getattr(value, "__name__", None)
    if isinstance(name, str):
        return name
    return type(value).__qualname__


def console_script(script_dir: str | os.PathLike[str], name: str,
                   source: str | None = None) -> Path:
    """Write a stand-in for console script `name` into `script_dir` and return its path.

    It has the name this system finds a console script by, so `moxi.paths.script(name)`
    finds it when the scripts directory is `script_dir`, and so does a PATH lookup
    (`shutil.which`, or a start by bare name) when `script_dir` is on PATH; and it runs.
    On POSIX it is `name`, a `/bin/sh` script with mode 0o755. On Windows it is
    `name.exe`, the launcher uv writes for a console script, copied from this
    environment's own scripts folder, with its script and its Python replaced. A test
    that needs the path uses the one returned, never `script_dir / name`.

    With no `source` it does nothing and exits 0. With `source`, Python code, it runs
    that code under this interpreter, with the stand-in's arguments in `sys.argv[1:]`
    exactly as the program that started it passed them, and exits with its status.
    `os.getpid()` in that code is the pid of the process that was started: on Windows
    the launcher runs Python as a child, so the stand-in reads its launcher's pid and
    answers that.
    """
    if WINDOWS:
        path = Path(script_dir, f"{name}.exe")
        script = "" if source is None else _STAND_IN_PID.format(exe=path.name) + source
        _write_launcher(path, sys.executable, script)
        return path
    python = sys.executable
    path = Path(script_dir, name)
    if source is None:
        path.write_text("#!/bin/sh\n")
    else:
        if "'" in python or "\\" in python:
            raise ValueError(f"can't quote {python!r} for both sh and Python")
        # Line 2 runs Python on this file in sh, and is a bare string to Python.
        path.write_text(f"#!/bin/sh\n\"exec\" '{python}' \"$0\" \"$@\"\n{source}")
    path.chmod(0o755)
    return path


# The head of a Windows stand-in's script. The launcher starts Python as its child (and a
# venv's python.exe starts the base interpreter as one more), so the script's own pid is
# not the one the product started: it walks up its parents to the nearest process with
# the stand-in's file name and makes `os.getpid` answer that pid.
_STAND_IN_PID = """\
def _stand_in_pid(exe):
    import ctypes, os
    from ctypes import wintypes
    class Entry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                    ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
                    ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                    ("parent", wintypes.DWORD), ("priority", ctypes.c_long),
                    ("flags", wintypes.DWORD), ("exe", ctypes.c_wchar * 260)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = kernel.CreateToolhelp32Snapshot(2, 0)
    if snap in (None, wintypes.HANDLE(-1).value):
        return None
    seen = {{}}
    try:
        entry = Entry(size=ctypes.sizeof(Entry))
        more = kernel.Process32FirstW(snap, ctypes.byref(entry))
        while more:
            seen[entry.pid] = (entry.parent, entry.exe.lower())
            more = kernel.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snap)
    pid = os.getpid()
    for _ in range(4):
        pid = seen.get(pid, (None, ""))[0]
        if pid is None:
            return None
        if seen.get(pid, (None, ""))[1] == exe:
            return pid
    return None
_pid = _stand_in_pid({exe!r}.lower())
if _pid is not None:
    import os as _os
    _os.getpid = lambda: _pid
del _stand_in_pid
"""

# The resources a uv console-script launcher reads (all RT_RCDATA): its kind (1 runs the
# zip in its own file as a script), the Python it starts, and that zip.
_LAUNCHER_KIND = "UV_TRAMPOLINE_KIND"
_LAUNCHER_PYTHON = "UV_PYTHON_PATH"
_LAUNCHER_SCRIPT = "UV_SCRIPT_DATA"
_RT_RCDATA = 10


def _write_launcher(path: Path, python: str, script: str) -> None:
    """Write a uv launcher to `path` that runs `script` under `python`.

    It starts from a copy of a launcher in this environment's scripts folder and replaces
    the resources it reads. The launcher starts `python` on its own file, which Python
    reads as a zip holding `__main__.py`, and hands it the command line as it got it."""
    import ctypes
    from ctypes import wintypes

    template, language = _launcher_template()
    shutil.copyfile(template, path)
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("__main__.py", script)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.BeginUpdateResourceW.restype = wintypes.HANDLE
    kernel.BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
    kernel.UpdateResourceW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.LPCWSTR,
                                       wintypes.WORD, ctypes.c_char_p, wintypes.DWORD]
    kernel.EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]
    delete_others = False
    update = kernel.BeginUpdateResourceW(str(path), delete_others)
    if not update:
        raise ctypes.WinError(ctypes.get_last_error())
    ok = True
    for resource, value in ((_LAUNCHER_PYTHON, os.fsencode(python)),
                            (_LAUNCHER_SCRIPT, data.getvalue())):
        ok = ok and bool(kernel.UpdateResourceW(update, _RT_RCDATA, resource, language,
                                                value, len(value)))
    error = ctypes.get_last_error()
    if not kernel.EndUpdateResourceW(update, not ok) or not ok:
        raise ctypes.WinError(error or ctypes.get_last_error())
    # Python finds the script as the last zip in the file; the update leaves the old one
    # in a later section, so the zip goes at the end as well, where the loader reads
    # nothing.
    with open(path, "ab") as exe_fh:
        exe_fh.write(data.getvalue())


@functools.cache
def _launcher_template() -> tuple[Path, int]:
    """A uv console-script launcher in this environment's scripts folder (moxi-dev's own
    first) and the language its script resource is stored under."""
    import ctypes
    from ctypes import wintypes

    scripts = Path(sysconfig.get_path("scripts"))
    found = sorted(scripts.glob("*.exe"), key=lambda p: p.name.lower() != "moxi-dev.exe")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LoadLibraryExW.restype = wintypes.HMODULE
    kernel.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
    kernel.FreeLibrary.argtypes = [wintypes.HMODULE]
    kernel.FindResourceW.restype = ctypes.c_void_p
    kernel.FindResourceW.argtypes = [wintypes.HMODULE, wintypes.LPCWSTR, ctypes.c_void_p]
    kernel.LoadResource.restype = ctypes.c_void_p
    kernel.LoadResource.argtypes = [wintypes.HMODULE, ctypes.c_void_p]
    kernel.LockResource.restype = ctypes.c_void_p
    kernel.LockResource.argtypes = [ctypes.c_void_p]
    kernel.SizeofResource.restype = wintypes.DWORD
    kernel.SizeofResource.argtypes = [wintypes.HMODULE, ctypes.c_void_p]
    languages = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p,
                                   ctypes.c_void_p, wintypes.WORD, ctypes.c_void_p)
    kernel.EnumResourceLanguagesW.argtypes = [wintypes.HMODULE, ctypes.c_void_p,
                                              wintypes.LPCWSTR, languages, ctypes.c_void_p]
    for exe in found:
        # As data only: nothing in the file runs.
        module = kernel.LoadLibraryExW(str(exe), None, 0x2 | 0x20)
        if not module:
            continue
        try:
            kind = kernel.FindResourceW(module, _LAUNCHER_KIND, _RT_RCDATA)
            if not kind or kernel.SizeofResource(module, kind) != 1:
                continue
            if ctypes.string_at(kernel.LockResource(kernel.LoadResource(module, kind)),
                                1) != b"\x01":
                continue
            seen: list[int] = []

            def note(_module: int, _type: int, _name: int, language: int,
                     _param: int) -> bool:
                seen.append(language)
                return True

            kernel.EnumResourceLanguagesW(module, _RT_RCDATA, _LAUNCHER_SCRIPT,
                                          languages(note), None)
            if seen:
                return exe, seen[0]
        finally:
            kernel.FreeLibrary(module)
    raise RuntimeError(f"no uv console-script launcher in {scripts} to copy for a "
                       "stand-in; install this environment with uv")


def toml_string(value: str | os.PathLike[str]) -> str:
    """`value`, a path or any text, as a TOML basic string, quotes included, that reads
    back as `value`. A test that writes a path into TOML writes it through this: a path
    put between quotes as it is breaks on Windows, where its backslashes read as escapes
    (C:\\Users holds the escape \\U), so the file doesn't parse or names another path."""
    text = os.fspath(value)
    out = ['"']
    for char in text:
        if char in ('"', "\\"):
            out.append("\\" + char)
        elif char == "\t" or (" " <= char and char != "\x7f"):
            out.append(char)
        else:
            out.append(f"\\u{ord(char):04X}")
    out.append('"')
    return "".join(out)


def write_sqlite(path: str | os.PathLike[str], schema: str = "",
                 rows: Mapping[str, Iterable[Sequence[Any]]] | None = None) -> Path:
    """Write a SQLite database for a test, fast: one transaction, no syncing.

    `schema` is SQL run as a script; `rows` maps a table to the rows inserted into it,
    each a sequence of column values in the table's order. All of it is one transaction
    with `PRAGMA synchronous=OFF` and the journal in memory, so either it all lands or
    nothing does. The file is an ordinary database afterwards."""
    path = Path(path)
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA journal_mode=MEMORY")
        conn.execute("BEGIN")
        try:
            if schema.strip():
                for statement in _statements(schema):
                    conn.execute(statement)
            for table, values in (rows or {}).items():
                values = [tuple(v) for v in values]
                if not values:
                    continue
                marks = ", ".join("?" * len(values[0]))
                quoted = '"' + table.replace('"', '""') + '"'
                conn.executemany(f"INSERT INTO {quoted} VALUES ({marks})", values)
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return path


def _statements(script: str) -> Iterator[str]:
    """Split a SQL script into complete statements, as SQLite reads them."""
    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            if pending.strip():
                yield pending
            pending = ""
    if pending.strip():
        yield pending


__all__ = ["ANCHOR_HOME_ENV", "BROWSERS_ENV", "CHANGED_ENV", "CHILD_ENV", "DURATIONS_ENV",
           "FINDING_ENV", "Fence", "FenceError", "GATE_CHAIN_S", "GATE_RESAMPLE_S",
           "GATE_SAMPLE_S", "GAUGES", "Gauge", "GaugeReading", "HARNESS_HOMES",
           "IDLE_ENV", "Idle", "KEPT_ENV", "LEAVE_OUT_ENV", "LIFTED", "LIVE_ENV",
           "MARKERS", "NOT_MEASURED", "NO_BYTE_CODE_ENV", "PARENT_GRACE_S",
           "PARENT_PID_ENV", "PARENT_POLL_S", "PROC_STAT", "PROC_WAIT_S",
           "PYCACHE_PREFIX_ENV", "REAL_SUPERVISOR_ENV", "RECORD_FORMAT", "SHUTDOWN_POLL",
           "STACKS_AT_ENV", "STACKS_ENV", "Seam", "Selection", "TIERS", "TIERS_ENV",
           "TIMED", "TIMED_CLOCKS", "TIMED_ENV", "TIMED_LOCK_FD_ENV", "TIMED_SLACK_ENV",
           "UV_DIRS_ENV", "UV_DOWNLOADS_ENV", "UV_PYTHON_ENV", "WINDOWS_HOMES",
           "WINDOWS_KEPT_ENV", "arm_stacks", "console_script", "fence", "findings_of",
           "idle", "param_id", "selection", "served", "short_dir", "started_env",
           "stat_counts", "terminal_record", "timed_reading", "timed_slack",
           "toml_string", "touches_match", "uv_env", "wait_for", "watch_parent",
           "write_sqlite"]
