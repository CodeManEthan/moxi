"""The crashed reading, from a run's server, its ended record and its server's end."""

from __future__ import annotations

import json

import pytest

from moxi_slots import registry, runs
from moxi_slots.model import ENDED, FREE, MISSING, OCCUPIED, Last, RunEnd
from moxi_slots.panes import ServerNow
from moxi_slots.registry import Registry, Row

pytestmark = pytest.mark.touches("packages/slots/src/moxi_slots/runs.py",
                                 "packages/slots/src/moxi_slots/registry.py")

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
    monkeypatch.setattr(runs, "_journal_misses", {})
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
    # A death the journal's last entry came before (its last writes lost in a hard
    # crash): still the shutdown's, since the window has no upper edge.
    (MISSING, last(EARLIER, died(5100, signal=1)), True),
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


def test_a_boot_the_journal_did_not_list_is_asked_about_again_later(monkeypatch):
    listed, clock = {}, [1000.0]
    monkeypatch.setattr(runs, "_journal_boots", lambda: dict(listed))
    monkeypatch.setattr(runs.time, "monotonic", lambda: clock[0])
    assert runs.journal_end(OLD_BOOT) is None
    listed[OLD_BOOT.replace("-", "")] = 5000.0
    clock[0] += runs.JOURNAL_RETRY_S - 1
    assert runs.journal_end(OLD_BOOT) is None
    clock[0] += 2
    assert runs.journal_end(OLD_BOOT) == 5000.0


def test_the_journal_is_read_before_the_module_lock_and_never_under_it(monkeypatch):
    held, reads = [False], []

    def boots():
        reads.append(held[0])
        return {OLD_BOOT.replace("-", ""): 5000.0}

    monkeypatch.setattr(runs, "_journal_boots", boots)
    record = last(EARLIER, died(4990, signal=1))
    with registry.locked():
        held[0] = True
        registry.write(Registry({1: Row(1.0, last=record)}))
        assert runs.journal_end(OLD_BOOT) is None
        assert not runs.crashed(MISSING, record, NOW)
        held[0] = False
    assert reads == []
    with registry.locked():
        held[0] = True
        assert runs.crashed(MISSING, registry.read().rows[1].last, NOW)
        held[0] = False
    assert reads == [False]
    assert runs.crashed(MISSING, record, NOW)
    assert reads == [False]


def test_a_journal_end_unread_under_the_lock_is_read_after_it(monkeypatch):
    reads = []
    monkeypatch.setattr(runs, "_journal_boots",
                        lambda: reads.append(1) or {OLD_BOOT.replace("-", ""): 5000.0})
    with runs.journal_closed():
        with runs.journal_closed():
            assert runs.journal_end(OLD_BOOT) is None
        assert runs.journal_end(OLD_BOOT) is None
    assert reads == []
    assert runs.journal_end(OLD_BOOT) == 5000.0
    assert reads == [1]


def test_rows_a_crashed_reading_would_not_ask_about_read_no_journal(monkeypatch):
    reads = []
    monkeypatch.setattr(runs, "_journal_boots",
                        lambda: reads.append(1) or {OLD_BOOT.replace("-", ""): 5000.0})
    settled = RunEnd(6000, None, None, settled="no session")
    registry.write(Registry({1: Row(1.0, last=last(EARLIER, died(4990, status=0))),
                             2: Row(1.0, last=last(EARLIER, settled)),
                             3: Row(1.0, last=last(EARLIER)),
                             4: Row(1.0, last=last(THIS_OLD, died(9950, signal=1))),
                             5: Row(1.0, last=last(None, died(4990, signal=1))),
                             6: Row(1.0)}))
    with registry.locked():
        pass
    assert reads == []
    rows = registry.read().rows
    rows[7] = Row(1.0, last=last(EARLIER, died(4990, signal=1)))
    registry.write(Registry(rows))
    with registry.locked():
        pass
    assert reads == [1]


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
