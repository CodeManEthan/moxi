"""The call log on disk: no secret ever written, collapse through the core's option, no
roll, and the 30-day deletion."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import stat

import pytest
from kit import TOKEN, bearer, call, request

from moxi_connector import calllog
from moxi_connector.calllog import CallLog
from moxi_connector.provider import FORBIDDEN, Provider, Result, Tool

DAY = 86400.0
#: 2026-11-04T10:44:00Z
T0 = dt.datetime(2026, 11, 4, 10, 44, tzinfo=dt.UTC).timestamp()


class Clock:
    def __init__(self, t: float = T0):
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def logdir(tmp_path):
    """A directory of the test's own: the fence keeps its home in the test's tmp_path."""
    out = tmp_path / "calls"
    out.mkdir()
    return out


def rows_of(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def every_byte(directory) -> bytes:
    return b"".join(p.read_bytes() for p in sorted(directory.iterdir()))


def test_the_day_file_is_private_and_holds_one_object_per_line(logdir):
    clock = Clock()
    log = CallLog(logdir / "day", clock=clock)
    calllog.event(log, "register", client="c1")
    log.write({"at": calllog.now(), "kind": "request", "status": 200}, durable=False,
              collapse=None)
    path = logdir / "day" / "2026-11-04.jsonl"
    assert [r["kind"] for r in rows_of(path)] == ["register", "request"]
    assert stat.S_IMODE(os.stat(logdir / "day").st_mode) == 0o700
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_the_writer_passes_the_refusal_and_durability_to_the_core(logdir, monkeypatch):
    from moxi import files

    seen = []
    real = files.append_record
    monkeypatch.setattr(files, "append_record",
                        lambda path, row, **kw: seen.append(kw) or real(path, row, **kw))
    log = CallLog(logdir, clock=Clock())
    log.write({"at": "x", "kind": "token"}, durable=True, collapse=None)
    log.write({"at": "x", "kind": "request"}, durable=False, collapse="401")
    assert seen[0]["max_bytes"] is None and seen[0]["refuse"] == FORBIDDEN
    assert seen[0]["durable"] is True and seen[0]["collapse"] is None
    assert seen[1]["durable"] is False and seen[1]["collapse"] == (log.window, "401")


@pytest.mark.parametrize("row", [
    {"kind": "request", "token": "t0ken-value"},
    {"kind": "request", "authorization": "Bearer t0ken-value"},
    {"kind": "request", "nested": {"code": "t0ken-value"}},
    {"kind": "request", "list": [{"text": "t0ken-value"}]},
    {"kind": "request", "query_string": "code=t0ken-value"},
])
def test_a_refused_field_at_any_depth_writes_nothing(logdir, row):
    log = CallLog(logdir, clock=Clock())
    with pytest.raises(ValueError):
        log.write({"at": "x", **row}, durable=True, collapse=None)
    with pytest.raises(ValueError):
        log.write({"at": "x", **row}, durable=False, collapse="401")
    assert b"t0ken-value" not in every_byte(logdir)


def test_no_token_or_code_the_door_saw_reaches_a_day_file(front, fake, logdir):
    """Requests carrying a token, a code in the query, an Authorization header and a body
    with a code; a provider that tries to log a code and a message body. Then every day
    file is searched for each secret."""
    secrets_used = [TOKEN, "bad-token-1234", "one-time-code-5678", "message body 9012"]

    async def leaky(args, ctx):
        with pytest.raises(ValueError):
            ctx.log(code="one-time-code-5678")
        with pytest.raises(ValueError):
            ctx.log(details={"text": "message body 9012"})
        ctx.log(sent=["m1"])
        return Result("sent")

    tool = Tool("leaky", "Logs too much.", {"type": "object"}, leaky)

    async def main():
        f = await front(providers={"demo": Provider(tools=(tool,))})
        f.host.calllog = CallLog(logdir, clock=Clock())
        await request(f, "POST", "/mcp", call("leaky"), headers=bearer())
        await request(f, "POST", "/mcp", call("leaky"), headers=bearer("bad-token-1234"))
        await request(f, "GET", "/authorize", headers=[("x-forwarded-for", "203.0.113.9")])
        await request(f, "POST", "/token",
                      raw=b"grant_type=authorization_code&code=one-time-code-5678")
        await request(f, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                          "params": {"name": "leaky", "arguments": {
                                              "text": "message body 9012"}}},
                      headers=bearer())
        f.host.calllog.close()
        await f.close()
    asyncio.run(main())
    data = every_byte(logdir)
    assert data, "the door wrote no row"
    for secret in secrets_used:
        assert secret.encode() not in data, secret
    rows = [json.loads(line) for line in data.splitlines()]
    assert {"sent": ["m1"]}.items() <= rows[0].items()
    assert rows[0]["grant"] and rows[1]["status"] == 401
    assert rows[2]["forwarded_for_untrusted"] == "203.0.113.9"
    for row in rows:
        assert not FORBIDDEN.intersection(row)


def test_rows_a_caller_without_a_token_causes_collapse_through_the_core(logdir):
    clock = Clock()
    log = CallLog(logdir, clock=clock)
    for _ in range(15):
        log.write({"at": calllog.now(), "kind": "request", "status": 401}, durable=False,
                  collapse="401")
    for _ in range(3):
        log.write({"at": calllog.now(), "kind": "request", "status": 404}, durable=False,
                  collapse="404")
    calllog.event(log, "token", grant="0a1b2c3d")
    clock.t += 60
    log.flush()
    rows = rows_of(logdir / "2026-11-04.jsonl")
    assert [r["status"] for r in rows if r["kind"] == "request"] == [401] * 10 + [404] * 3
    [summary] = [r for r in rows if r["kind"] == "summary"]
    assert (summary["class"], summary["count"], summary["per"]) == ("401", 5, "minute")
    assert summary["since"] == "2026-11-04T10:44:00.000Z"
    # An event is never collapsed, whatever else is busy.
    assert [r for r in rows if r["kind"] == "token"]


def test_close_writes_the_pending_summary(logdir):
    log = CallLog(logdir, clock=Clock())
    for _ in range(12):
        log.write({"at": "x", "kind": "request"}, durable=False, collapse="421")
    log.close()
    [summary] = [r for r in rows_of(logdir / "2026-11-04.jsonl") if r["kind"] == "summary"]
    assert summary["count"] == 2


def test_no_file_rolls_past_a_megabyte(logdir):
    log = CallLog(logdir, clock=Clock())
    pad = "x" * 1000
    for n in range(1200):
        log.write({"at": "x", "kind": "request", "n": n, "pad": pad}, durable=False,
                  collapse=None)
    assert sorted(p.name for p in logdir.iterdir()) == ["2026-11-04.jsonl"]
    path = logdir / "2026-11-04.jsonl"
    assert path.stat().st_size > 1_100_000
    assert [r["n"] for r in rows_of(path)] == list(range(1200))


def _days(directory, first: int, last: int) -> list[str]:
    names = []
    for back in range(first, last + 1):
        name = (dt.date(2026, 11, 4) - dt.timedelta(days=back)).isoformat() + ".jsonl"
        (directory / name).write_text("{}\n")
        names.append(name)
    return names


def test_files_older_than_thirty_days_are_deleted_and_nothing_else(logdir):
    names = _days(logdir, 0, 40)
    (logdir / "notes.txt").write_text("kept")
    (logdir / "2026-13-99.jsonl").write_text("kept")
    gone = CallLog(logdir, clock=Clock()).prune()
    assert sorted(gone) == sorted(names[30:])
    left = sorted(p.name for p in logdir.iterdir())
    assert left == sorted([*names[:30], "notes.txt", "2026-13-99.jsonl"])


def test_the_deletion_runs_again_at_utc_midnight(logdir):
    clock = Clock(dt.datetime(2026, 11, 4, 23, 59, 50, tzinfo=dt.UTC).timestamp())
    names = _days(logdir, 0, 29)
    log = CallLog(logdir, clock=clock)
    log.prune()
    log.flush()
    assert len(list(logdir.iterdir())) == 30
    clock.t += 20                       # 2026-11-05T00:00:10Z
    log.flush()
    assert not (logdir / names[-1]).exists()
    assert (logdir / names[-2]).exists()


def test_a_minute_open_at_midnight_is_summed_in_its_own_days_file(logdir):
    clock = Clock(dt.datetime(2026, 11, 4, 23, 59, 30, tzinfo=dt.UTC).timestamp())
    log = CallLog(logdir, clock=clock)
    for _ in range(11):
        log.write({"at": "x", "kind": "request"}, durable=False, collapse="404")
    clock.t += 40
    log.flush()
    assert [r["kind"] for r in rows_of(logdir / "2026-11-04.jsonl")][-1] == "summary"
    assert not (logdir / "2026-11-05.jsonl").exists()


def test_read_rows_reads_days_oldest_first_and_skips_bad_lines(logdir):
    (logdir / "2026-11-03.jsonl").write_text('{"kind": "a"}\nnot json\n[1]\n')
    (logdir / "2026-11-04.jsonl").write_text('{"kind": "b"}\n{"kind": "check"}\n')
    rows = list(calllog.read_rows(days=2, now=T0, directory=logdir))
    assert [r["kind"] for r in rows] == ["a", "b", "check"]
    assert [r["kind"] for r in calllog.read_rows(now=T0, directory=logdir)] == ["b", "check"]
    assert calllog.last({"a"}, now=T0, directory=logdir) == {"kind": "a"}
    assert calllog.last({"zzz"}, now=T0, directory=logdir) is None
