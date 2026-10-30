"""The registry file and the module lock."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

from moxi_slots import places, registry
from moxi_slots.errors import RegistryError, TryAgain
from moxi_slots.model import Hold, Last
from moxi_slots.registry import Registry, Row

pytestmark = pytest.mark.touches("packages/slots/src/moxi_slots/registry.py")


def test_no_file_is_an_empty_registry():
    assert registry.read().rows == {}


def test_a_registry_reads_back_as_written():
    later = time.time() + 600
    reg = Registry({3: Row(100.0, "review", Hold("run-1", later),
                           Last(200.0, "agent", {"session": "s1"})),
                    12: Row(150.0)})
    registry.write(reg)
    assert oct(os.stat(places.registry()).st_mode & 0o777) == "0o600"
    back = registry.read()
    assert back.rows == reg.rows
    assert back.by_name("review") == 3 and back.by_name("other") is None
    data = json.loads(places.registry().read_text())
    assert data["version"] == 1 and set(data["slots"]) == {"3", "12"}


def test_a_write_drops_holds_that_ran_out():
    reg = Registry({1: Row(1.0, hold=Hold("old", time.time() - 1)),
                    2: Row(1.0, hold=Hold("new", time.time() + 60))})
    assert [n for n, _ in registry.expired_holds(reg)] == [1]
    registry.write(reg)
    back = registry.read()
    assert back.rows[1].hold is None and back.rows[2].hold.holder == "new"


@pytest.mark.parametrize("text", ["not json", "[]", '{"version": 2, "slots": {}}',
                                  '{"version": 1}', '{"version": 1, "slots": {"0": {}}}',
                                  '{"version": 1, "slots": {"x": {"created": 1}}}',
                                  '{"version": 1, "slots": {"1": {"name": "a"}}}',
                                  '{"version": 1, "slots": {"1": {"created": 1, '
                                  '"hold": {"holder": "h"}}}}'])
def test_a_registry_slots_cannot_read_raises_naming_the_file(text):
    places.registry().parent.mkdir(parents=True, exist_ok=True)
    places.registry().write_text(text)
    with pytest.raises(RegistryError, match="slots.json"):
        registry.read()


def test_the_lock_not_had_in_its_wait_is_try_again():
    places.module_lock().parent.mkdir(parents=True, exist_ok=True)
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time\n"
         "from moxi import files\n"
         f"with files.lock({str(places.module_lock())!r}):\n"
         "    print('held', flush=True)\n"
         "    time.sleep(30)\n"],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(TryAgain, match="try again"):
            with registry.locked(timeout=0.2):
                pass
    finally:
        holder.kill()
        holder.wait(timeout=5)
    with registry.locked(timeout=5):
        pass
