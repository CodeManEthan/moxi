"""`moxi cache segment`: the segment printed under the core's render audit hook
(`moxi.render`, whose own tests are the core's). An audit hook cannot be removed, so every
test that installs one runs in a child process."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time

import pytest

from moxi_cache import records

SID = "aaaaaaaa-1111-4111-8111-111111111111"


def child(code: str, *args, cwd=None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code), *args],
                          capture_output=True, text=True, timeout=60, cwd=cwd,
                          env=dict(os.environ))


def put_record(age=125.0, seen_ago=0.0, kind="warm", **thresholds):
    now = time.time()
    records_dir = records.record_dir()
    records_dir.mkdir(parents=True, exist_ok=True)
    rec = {"v": 1, "session": SID, "harness": "claude", "pid": 1, "kind": kind,
           "last_request": now - age, "ttl": 3600, "read_at": now, "seen_at": now - seen_ago,
           "code": None, "warn_before": 900, "stale_after": 30}
    rec.update(thresholds)
    for name, value in thresholds.items():
        if value is None:
            del rec[name]
    (records_dir / f"{SID}.json").write_text(json.dumps(rec))


def test_cache_keeps_no_render_hook_of_its_own():
    from importlib.util import find_spec

    assert find_spec("moxi_cache.render") is None


def test_loading_the_api_and_the_cli_does_not_load_the_segment():
    proc = child('''
        import sys
        import moxi_cache.api, moxi_cache.cli
        from moxi import modules
        assert modules.load("cache").state == "present"
        print("moxi_cache.segment" in sys.modules)
    ''')
    assert proc.stdout.strip() == "False", proc.stderr


def run_segment(*args) -> subprocess.CompletedProcess:
    code = '''
        import sys
        from moxi import cli
        sys.exit(cli.main(["cache", "segment", *sys.argv[1:]]))
    '''
    return child(code, *args)


def test_moxi_cache_segment_prints_the_text():
    put_record(age=125)
    proc = run_segment(SID)
    assert proc.returncode == 0 and proc.stdout == "cache:2m5s\n", proc.stderr


def test_moxi_cache_segment_colour_and_dim():
    put_record(age=3700, kind="cold", seen_ago=100)
    proc = run_segment(SID, "--color")
    assert proc.stdout == "\033[2m\033[31mcache:1h1m\033[0m\n", proc.stderr


def test_moxi_cache_segment_json():
    put_record(age=60)
    doc = json.loads(run_segment(SID, "--json").stdout)
    assert doc["text"] == "cache:1m0s" and doc["band"] == "warm" and doc["dim"] is False
    assert set(doc) == {"text", "band", "age_s", "ttl_s", "dim"}


def test_moxi_cache_segment_bands_and_dims_by_the_records_own_values():
    put_record(age=125, seen_ago=100, warn_before=3500, stale_after=600)
    proc = run_segment(SID, "--color")
    assert proc.stdout == "\033[33mcache:2m5s\033[0m\n", proc.stderr
    put_record(age=3000, seen_ago=10, warn_before=60, stale_after=5)
    proc = run_segment(SID, "--color")
    assert proc.stdout == "\033[2m\033[32mcache:50m0s\033[0m\n", proc.stderr


@pytest.mark.parametrize("missing", ["warn_before", "stale_after"])
def test_moxi_cache_segment_prints_nothing_for_a_record_lacking_a_threshold(missing):
    put_record(age=125, **{missing: None})
    proc = run_segment(SID)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")
    assert run_segment(SID, "--json").stdout == "null\n"


def test_moxi_cache_segment_with_no_record_prints_nothing():
    proc = run_segment(SID)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")
    assert run_segment(SID, "--json").stdout == "null\n"


def test_moxi_cache_segment_refuses_a_segment_module_that_writes(tmp_path):
    (tmp_path / "evil_segment.py").write_text(textwrap.dedent(f'''
        open({str(tmp_path / "written")!r}, "w").write("x")

        def segment(session_id, now=None):
            return None
    '''))
    proc = child('''
        import sys
        sys.path.insert(0, sys.argv[1])
        import moxi_cache.module
        moxi_cache.module.SEGMENT = "evil_segment"
        from moxi_cache import cli
        code = cli.main(["segment", "x"], prog="moxi cache")
        from moxi import render
        print(render.installed())
        sys.exit(code)
    ''', str(tmp_path))
    assert proc.returncode == 1
    assert "the render was refused" in proc.stderr and "for writing" in proc.stderr
    # The hook that refused it is the core's.
    assert proc.stdout.strip() == "True"
    assert not (tmp_path / "written").exists()


def test_the_announcement_names_the_segment_module():
    from moxi import modules

    assert modules.load("cache").segment == "moxi_cache.segment"


@pytest.mark.parametrize("args", [["--color", "--json"]])
def test_colour_and_json_are_one_or_the_other(args):
    proc = run_segment(SID, *args)
    assert proc.returncode == 2
