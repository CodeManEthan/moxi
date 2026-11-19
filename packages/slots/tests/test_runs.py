"""The crashed reading, from a run's server, its ended record and its server's end."""

from __future__ import annotations

import json

import pytest

from moxi_slots import runs
from moxi_slots.model import ENDED, FREE, MISSING, OCCUPIED, Last, RunEnd
from moxi_slots.panes import ServerNow

pytestmark = pytest.mark.touches("packages/slots/src/moxi_slots/runs.py")

BOOT = "11111111-2222-3333-4444-555555555555"
OLD_BOOT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
#: The server answering now: started at 10000 in this boot.
NOW = ServerNow(pid=700, start_time=10000)
#: The journal read and the boot id, before the fixture stands in for them.
_real_boots = runs._journal_boots
_real_boot_id = runs.boot_id


@pytest.fixture(autouse=True)
def boots(monkeypatch):
    """This boot is `BOOT`; the journal says the old boot's last entry was at 5000."""
    monkeypatch.setattr(runs, "boot_id", lambda: BOOT)
    monkeypatch.setattr(runs, "_journal_ends", {})
    monkeypatch.setattr(runs, "_journal_boots",
                        lambda: {OLD_BOOT.replace("-", ""): 5000.0})


def last(server, ended=None, *, trusted=True) -> Last:
    return Last(1.0, "agent", None, "tok", server, trusted, ended)


def died(at, status=None, signal=None) -> RunEnd:
    return RunEnd(at, status, signal)


THIS_OLD = f"{BOOT}:600"
EARLIER = f"{OLD_BOOT}:600"


@pytest.mark.parametrize("state,record,crashed", [
    # No end seen under an old server: the server went down with the run.
    (MISSING, last(EARLIER), True),
    (MISSING, last(THIS_OLD), True),
    (FREE, last(THIS_OLD), True),
    # A signal within 120 s before the old server's end; long before it.
    (MISSING, last(EARLIER, died(4900, signal=1)), True),
    (MISSING, last(EARLIER, died(4880, signal=15)), True),
    (MISSING, last(EARLIER, died(4879, signal=15)), False),
    (MISSING, last(THIS_OLD, died(9950, status=143)), True),
    (MISSING, last(THIS_OLD, died(9000, status=143)), False),
    # Ended by itself: status 0, no signal, trusted.
    (MISSING, last(EARLIER, died(4990, status=0)), False),
    # An untrusted run: the window is the whole test.
    (MISSING, last(EARLIER, died(4990, status=0), trusted=False), True),
    (MISSING, last(EARLIER, died(4000, status=0), trusted=False), False),
    # The current server, an occupied slot, no server recorded, no last launch.
    (ENDED, last(f"{BOOT}:700"), False),
    (OCCUPIED, last(EARLIER), False),
    (MISSING, last(None), False),
    (MISSING, None, False),
    # Settled.
    (MISSING, last(EARLIER, RunEnd(6000, None, None, settled="no session")), False),
])
def test_crashed(state, record, crashed):
    assert runs.crashed(state, record, NOW) is crashed


def test_no_server_answering_is_never_crashed():
    assert runs.crashed(MISSING, last(EARLIER), None) is False


def test_with_the_journal_unread_only_a_run_with_no_end_is_crashed(monkeypatch):
    monkeypatch.setattr(runs, "_journal_boots", lambda: {})
    assert runs.crashed(MISSING, last(EARLIER), NOW)
    assert not runs.crashed(MISSING, last(EARLIER, died(4990, signal=1)), NOW)
    assert not runs.crashed(MISSING, last(EARLIER, died(4990, status=0), trusted=False),
                            NOW)
    assert runs.server_end(EARLIER, NOW) is None


def test_this_boots_server_ends_when_the_current_one_started():
    assert runs.server_end(THIS_OLD, NOW) == 10000.0
    assert runs.server_end(THIS_OLD, ServerNow(700, None)) is None
    assert runs.server_end(EARLIER, NOW) == 5000.0


def test_the_journal_is_read_once_per_process(monkeypatch):
    seen = []

    def boots():
        seen.append(1)
        return {OLD_BOOT.replace("-", ""): 5000.0}

    monkeypatch.setattr(runs, "_journal_boots", boots)
    assert runs.journal_end(OLD_BOOT) == 5000.0
    assert runs.journal_end(OLD_BOOT.upper()) == 5000.0
    assert runs.journal_end("not-a-boot") is None
    assert runs.journal_end("not-a-boot") is None
    assert len(seen) == 2


def test_the_journal_read_takes_list_boots_json(monkeypatch, fence):
    tool = fence.short / "bin" / "journalctl"
    tool.parent.mkdir(parents=True)
    tool.write_text("#!/bin/sh\n" + "echo '" + json.dumps(
        [{"index": -1, "boot_id": OLD_BOOT.replace("-", ""), "first_entry": 1,
          "last_entry": 5_000_000_000}]) + "'\n")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(tool.parent))
    monkeypatch.setattr(runs, "_journal_boots", _real_boots)
    assert runs.journal_end(OLD_BOOT) == 5000.0


def test_a_journal_that_fails_is_unknown(monkeypatch, fence):
    tool = fence.short / "bin" / "journalctl"
    tool.parent.mkdir(parents=True)
    tool.write_text("#!/bin/sh\necho 'not json'\n")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(tool.parent))
    monkeypatch.setattr(runs, "_journal_boots", _real_boots)
    assert runs.journal_end(OLD_BOOT) is None


def test_the_boot_id_is_this_machines():
    real = _real_boot_id()
    assert real is None or len(real.replace("-", "")) == 32


def test_server_ids_need_both_parts():
    assert runs.server_id(5, "b") == "b:5"
    assert runs.server_id(None, "b") is None
    assert runs.server_id(5) == f"{BOOT}:5"
