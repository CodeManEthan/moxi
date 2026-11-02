"""The acts: add, remove and repair, and the live comparison `check`.

Every act runs in four steps, with one lock order (the core lock before `tunnel.lock`) and
no lock held while the user is asked:

1. plan, with no lock: read the record and the live config, choose the port, check the
   provider's prerequisites and build the warning lines. An add that is already on and
   matches the live config stops here and changes nothing;
2. confirm, with no lock;
3. lock and plan again: take the locks the plan needs, read again and plan again. When
   what the user confirmed differs, tunnel lets the locks go and asks once more; when
   only the locks differ (the act now touches a Cloudflare entry), it takes both without
   asking. A plan that changes a second time exits 75;
4. do the act under the locks, keeping `before` (the record and the live config step 3
   read). Every record write is a `record.commit`. Then the locks are let go and the
   change listeners are called.

An add commits its entry `applying`, applies, reads the live config back and commits it
`on`. A remove commits `removing`, removes, reads back and drops the entry once its live
config is gone. On a failure after the first change tunnel reads the live config again for
each port the act touched: when it equals `before`'s, the act's entries go back whole (with
new generations) and a `rollback` is logged; otherwise the entry stays `applying` or
`removing` with the provider's stderr in `detail`, `check` reads it `half`, and `repair`
finishes it. Either way the act exits 1 with the provider's stderr unchanged.

A `--replace` is finished or undone as one unit, and the foreign config it takes over is
never lost: at every moment it is live or a copy of it is in `displaced/`.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import NoReturn

from moxi import exit as codes

from . import api, changes, confirm as warn, declare, locks, ports, provider, record
from . import tailscale as ts_
from .errors import ApplyError, Busy, Refused, TunnelError, UsageError
from .record import NEWER, Entry, Record, State, normal_host

Confirm = Callable[[list[str]], bool]

#: The check states, in their order of precedence.
CHECKS = ("orphan", "half", "renamed", "missing", "changed", "clash", "moved", "ok")


@dataclass(frozen=True)
class EntryState:
    """One recorded target against the live config and its settings now."""

    target: str
    check: str
    entry: Entry
    detail: str | None = None
    fix: str | None = None


@dataclass
class Outcome:
    """What an act did: the target's address after it, the lines it reports, its exit
    code."""

    address: api.Address | None = None
    notes: list[str] = field(default_factory=list)
    code: int = codes.OK


def _step(name: str) -> None:
    """A point between two steps of an act. Tests stop an act here to leave the state a
    crash would."""


# -- reading ------------------------------------------------------------------------------

def _writable(rec: Record) -> Record:
    if rec.exists and not rec.writable:
        raise Refused(f"tunnel's record {record.record_path()} has format "
                      f"{rec.format!r}, which this version doesn't write",
                      "upgrade moxi-tunnel")
    return rec


def _bad_keys(decls: declare.Declarations) -> dict[str, declare.Bad]:
    out = {}
    for bad in decls.bad:
        module, dot, name = bad.entry.partition(".")
        if dot:
            out[f"{module}/{name}"] = bad
    return out


def _ports_now(rec: Record, decls: declare.Declarations) -> dict[str, int]:
    """Each recorded target's loopback port now: its setting, else the record's."""
    out = {}
    for key, entry in rec.entries.items():
        if not entry.known:
            continue
        target = decls.targets.get(key)
        port = entry.target
        if target is not None:
            try:
                port = declare.loopback_port(target)
            except Refused:
                pass
        out[key] = port
    return out


def _hand_off(entry: Entry) -> str:
    return f"tailscale {ts_.verb(entry.reach)} --https={entry.port} off"


def _reason(exc: BaseException) -> str:
    return exc.reason if isinstance(exc, TunnelError) else f"{type(exc).__name__}: {exc}"


def _hostport_present(serve: Mapping, entry: Entry) -> bool:
    key = f"{normal_host(entry.host)}:{entry.port}"
    got = ts_.parts(serve, entry.port)
    return key in got.get("Web", {}) or key in got.get("AllowFunnel", {})


def _states(rec: Record, decls: declare.Declarations, keys, live: Callable) -> list[EntryState]:
    ports_now = _ports_now(rec, decls)
    clash = declare.clashes(ports_now)
    bad = _bad_keys(decls)
    out = []
    for key in sorted(keys):
        entry = rec.entries[key]
        out.append(_state(key, entry, decls, bad, ports_now, clash, live))
    return out


def _state(key, entry, decls, bad, ports_now, clash, live) -> EntryState:
    if not entry.known:
        return EntryState(key, "changed", entry, NEWER, "upgrade moxi-tunnel")
    if key not in decls.targets and key not in bad:
        return EntryState(key, "orphan", entry, "the module that declared it is gone",
                          f"moxi tunnel remove {key}")
    if entry.state != State.ON:
        return EntryState(key, "half", entry, entry.detail or f"left {entry.state}",
                          "moxi tunnel repair")
    got = live(entry)
    if got == "renamed":
        return EntryState(key, "renamed", entry, "the machine's name changed",
                          "moxi tunnel repair")
    if got == "missing":
        return EntryState(key, "missing", entry, "its live config is gone",
                          "moxi tunnel repair")
    if got is not None:
        return EntryState(key, "changed", entry, got if got != "changed" else
                          "its live config no longer matches what Moxi applied",
                          f"{_hand_off(entry)}, then moxi tunnel remove {key} --forget"
                          if entry.provider == "tailscale" else "moxi tunnel repair")
    if key in clash:
        return EntryState(key, "clash", entry, f"its loopback port {ports_now[key]} is also "
                          f"{', '.join(clash[key])}'s", _clash_fix(key, clash[key], decls))
    if ports_now.get(key, entry.target) != entry.target:
        return EntryState(key, "moved", entry, f"its loopback port moved from {entry.target} "
                          f"to {ports_now[key]}", "moxi tunnel repair")
    return EntryState(key, "ok", entry)


def _clash_fix(key: str, others: list[str], decls: declare.Declarations) -> str:
    names = []
    for k in [key, *others]:
        t = decls.targets.get(k)
        names.append(f"{t.module} {t.port_setting}" if t else k)
    return "give these settings different ports: " + "; ".join(names)


class _Live:
    """The live-state reader for `check` and the plans: one provider each, read once."""

    def __init__(self, deadline: float | None = None):
        self.deadline = deadline
        self.providers: dict[str, provider.Provider] = {}

    def get(self, name: str) -> provider.Provider:
        if name not in self.providers:
            self.providers[name] = provider.get(name, deadline=self.deadline)
        return self.providers[name]

    def __call__(self, entry: Entry) -> str | None:
        try:
            return self.get(entry.provider).live_state(entry)
        except Refused as exc:
            return exc.reason


def check(module: str | None = None, name: str | None = None, *,
          deadline: float | None = None) -> list[EntryState]:
    """Compare each recorded entry (or the one `<module>/<name>`) with the live config
    and its settings now, taking no lock. Each provider is read once for all the
    entries, within `deadline` (a `time.monotonic()` value) when one is given."""
    rec = record.read()
    decls = declare.read()
    keys = list(rec.entries)
    if module is not None:
        keys = [k for k in keys if k == f"{module}/{name}"]
    return _states(rec, decls, keys, _Live(deadline))


# -- the act runner -----------------------------------------------------------------------

@dataclass
class _Plan:
    lines: list[str] = field(default_factory=list)
    signature: object = None
    core: bool = False
    done: Outcome | None = None
    data: dict = field(default_factory=dict)


class _Act:
    """One act's records: the record before it and the last one it wrote."""

    def __init__(self, before: Record):
        self.before = before
        self.last: Record | None = None

    def commit(self, entries: Mapping[str, Entry | None], **kw) -> Record:
        self.last = record.commit(entries, **kw)
        return self.last

    def current(self, key: str) -> Entry | None:
        rec = self.last or self.before
        return rec.entries.get(key)


def _run(plan: Callable[[], _Plan], do: Callable[[_Plan, _Act], Outcome],
         ask: Confirm) -> Outcome:
    first = plan()
    if first.done is not None:
        return first.done
    asked = None
    changed = 0
    core = first.core
    current = first
    while True:
        if asked != current.signature:
            locks.none_held()
            if not ask(list(current.lines)):
                raise Refused("not confirmed; nothing was changed",
                              "run it again and answer y, or add --yes")
            asked = current.signature
        act: _Act | None = None
        try:
            with locks.held(core=core):
                again = plan()
                if again.done is not None:
                    return again.done
                if again.core and not core:
                    core = True
                    current = again
                    continue
                if again.signature != asked:
                    changed += 1
                    if changed > 1:
                        raise Busy("what this act would do changed twice while it waited",
                                   "run it again")
                    current = again
                    continue
                act = _Act(again.data["rec"])
                return do(again, act)
        finally:
            if act is not None and act.last is not None:
                changes.notify(act.before, act.last)


def _fail(act: _Act, ts: ts_.Tailscale, exc: BaseException, *, key: str, ports_: list[int],
          before_serve: Mapping, restore: Entry | None, kind: str,
          pending: str | None = None) -> NoReturn:
    """A failure after the act's first change: go back whole when the live config is as
    `before` had it on every port the act touched, else leave the entry with `detail`."""
    stderr = _reason(exc)
    try:
        now = ts.serve_config(fresh=True)
    except TunnelError:
        now = None
    if now is not None and all(ts_.parts(now, p) == ts_.parts(before_serve, p)
                               for p in ports_):
        act.commit({key: restore})
        if pending is not None:
            _settle(pending, ts, act, finishing=False)
        record.event("rollback", key, provider=ts.name, port=ports_[0], act=kind)
        raise ApplyError(stderr) from exc
    cur = act.current(key)
    if cur is not None:
        act.commit({key: replace(cur, detail=stderr)})
    if pending is not None and now is not None and record.is_pending(pending):
        copy = record.read_copy(pending)
        if not ts_.parts(now, int(copy["port"])):
            record.rename_pending(pending)
    raise ApplyError(stderr, "moxi tunnel repair") from exc


# -- add ----------------------------------------------------------------------------------

def _refuse_problems(problems: list[provider.Problem], what: str) -> NoReturn:
    text = "; ".join(f"{p.reason} (fix: {p.fix})" for p in problems)
    raise Refused(f"{what} is not ready: {text}", problems[0].fix)


def _resolve_declared(text: str, decls: declare.Declarations) -> str:
    bad = _bad_keys(decls)
    keys = set(decls.targets) | set(bad)
    key = declare.resolve(text, keys)
    if key in bad:
        raise Refused(f"the declaration of {key} is bad: {bad[key].reason}", bad[key].fix)
    return key


def add(text: str, *, provider_name: str | None = None, port: int | None = None,
        hostname: str | None = None, replace_: bool = False, ask: Confirm) -> Outcome:
    """Turn target `text` on, or report that it is on. Raises `Refused` (exit 3),
    `ApplyError` (exit 1), `UsageError` (exit 2), `Busy` or `LockTimeout` (exit 75)."""
    if provider_name == "cloudflare" and replace_:
        raise UsageError("--replace is for Tailscale ports only")
    if hostname is not None and provider_name != "cloudflare":
        raise UsageError("--hostname is for --provider cloudflare")
    if port is not None and provider_name == "cloudflare":
        raise UsageError("--port is for Tailscale; a Cloudflare target is on 443")

    def plan() -> _Plan:
        rec = _writable(record.read())
        decls = declare.read()
        key = _resolve_declared(text, decls)
        target = decls.targets[key]
        old = rec.entries.get(key)
        if old is not None and not old.known:
            raise Refused(f"the entry for {key} was {NEWER}", "upgrade moxi-tunnel")
        if record.names_pending(old):
            raise Refused(f"{key} has a replace that has not settled",
                          f"moxi tunnel repair finishes it; moxi tunnel remove {key} "
                          "undoes it")
        name = provider_name or (old.provider if old else provider.DEFAULT)
        prov = provider.get(name)
        if old is not None and old.provider != name:
            provider.get(old.provider)
            raise Refused(f"{key} is on {old.provider}; switching it to {name} needs the "
                          "Cloudflare provider, which this version doesn't have",
                          "upgrade moxi-tunnel")
        loop = declare.loopback_port(target)
        others = {k: p for k, p in _ports_now(rec, decls).items() if k != key}
        same = declare.same_port(loop, others, exclude=key)
        if same:
            raise Refused(f"{key}'s loopback port {loop} is also {same[0]}'s",
                          f"moxi config set {target.module} {target.port_setting} <port>")
        problems = prov.ready(target.reach)
        if problems:
            _refuse_problems(problems, "Tailscale")
        host = prov.host()
        serve = prov.serve_config()
        if old is not None:
            if (old.state == State.ON and normal_host(old.host) == host
                    and ts_.matches(serve, old, target=loop)):
                if port is not None and port != old.port:
                    raise Refused(f"{key} is already on port {old.port}",
                                  f"moxi tunnel remove {key}, then add it with --port "
                                  f"{port}")
                return _Plan(done=Outcome(api.from_entry(rec, key),
                                          [f"{key} is already on: "
                                           f"{api.url(old.host, old.port)}"]))
            raise Refused(f"{key} is recorded but not ok as it is",
                          f"moxi tunnel repair, or moxi tunnel remove {key}")
        moxi = ports.moxi_ports(rec, exclude=key)
        foreign = {p: ts_.describe(ts_.parts(serve, p)) for p in ts_.tcp_ports(serve)
                   if p not in moxi}
        funnel = prov.funnel_ports() if target.reach == "public" else None
        choice = ports.choose(target.reach, target=loop, port=port, moxi=moxi,
                              foreign=foreign, funnel_ports=funnel, replace=replace_)
        entry = Entry(reach=target.reach, provider=name, host=host, port=choice.port,
                      target=loop, state=State.APPLYING)
        url = api.url(host, choice.port)
        lines = [f"{key}: {ts_.verb(target.reach)} {url} to {ts_.upstream(loop)} "
                 f"({entry.exposure})", *choice.lines]
        more, ct = warn.add_lines(provider=name, host=host, url=url, module=target.module,
                                  reach=target.reach, exposure=entry.exposure,
                                  warned=rec.warned_ct)
        lines += more
        copy = None
        if choice.replace:
            copy = ts_.copy_of(serve, choice.port, host)
            lines.append(warn.replace_line(choice.port, copy, str(record.displaced_dir())))
        signature = ("add", key, json.dumps(entry.to_json(), sort_keys=True),
                     json.dumps(copy, sort_keys=True), tuple(lines))
        return _Plan(lines, signature, core=name == "cloudflare",
                     data=dict(rec=rec, key=key, entry=entry, ct=ct, copy=copy, ts=prov,
                               serve=serve))

    return _run(plan, _do_add, ask)


def _do_add(plan: _Plan, act: _Act) -> Outcome:
    d = plan.data
    key, ts, copy = d["key"], d["ts"], d["copy"]
    entry = replace(d["entry"], applied_at=record.stamp())
    old = d["rec"].entries.get(key)
    warned = [entry.host] if d["ct"] else []
    notes = []
    if copy is not None:
        port = entry.port
        name = record.write_pending(port, copy)
        _step("replace-copy")
        entry = replace(entry, displaced=name)
        act.commit({key: entry}, warned=warned)
        _step("replace-applying")
        try:
            now = ts.serve_config(fresh=True)
        except TunnelError as exc:
            _fail(act, ts, exc, key=key, ports_=[port], before_serve=d["serve"],
                  restore=old, kind="replace", pending=name)
        if ts_.parts(now, port) != copy["parts"]:
            act.commit({key: old})
            record.delete_pending(name)
            raise Busy(f"port {port}'s config changed after you confirmed; nothing was "
                       "changed", "run moxi tunnel add again")
        try:
            ts.remove_copy(copy)
            _step("replace-removed")
            now = ts.serve_config(fresh=True)
        except Exception as exc:  # noqa: BLE001 - every failure goes back or is kept
            _fail(act, ts, exc, key=key, ports_=[port], before_serve=d["serve"],
                  restore=old, kind="replace", pending=name)
        left = ts_.parts(now, port)
        if left:
            detail = (f"the removal left part of the foreign config on port {port}: "
                      f"{ts_.describe(left)}")
            act.commit({key: replace(entry, detail=detail)})
            raise ApplyError(detail, "moxi tunnel repair")
        record.rename_pending(name)
        record.event("replace", key, provider=ts.name, port=port, copy=name)
        _step("replace-renamed")
        notes.append(f"the foreign config on port {port} is kept in "
                     f"{record.final_path(name)}")
    else:
        act.commit({key: entry}, warned=warned)
        _step("add-applying")
    try:
        ts.apply(entry)
        _step("add-applied")
        now = ts.serve_config(fresh=True)
        if not ts_.matches(now, entry):
            raise ApplyError(f"after the apply, port {entry.port} does not hold what Moxi "
                             f"applied: {ts_.describe(ts_.parts(now, entry.port))}")
    except Exception as exc:  # noqa: BLE001 - every failure goes back or is kept
        _fail(act, ts, exc, key=key, ports_=[entry.port], before_serve=d["serve"],
              restore=old, kind="add")
    after = act.commit({key: replace(entry, state=State.ON, detail=None)})
    record.event("add", key, provider=ts.name, port=entry.port, new_host=entry.host)
    address = api.from_entry(after, key)
    return Outcome(address, [f"{key}: {address.url} ({address.exposure})", *notes])


# -- remove -------------------------------------------------------------------------------

def remove(text: str, *, ask: Confirm, forget: bool = False) -> Outcome:
    """Turn target `text` off, or with `forget` drop it from the record and leave its
    live config alone. An entry that names a pending copy is undone: nothing of it is
    live, so no provider command runs."""

    def plan() -> _Plan:
        rec = _writable(record.read())
        decls = declare.read()
        key = declare.resolve(text, set(decls.targets) | set(rec.entries)
                              | set(_bad_keys(decls)))
        old = rec.entries.get(key)
        if old is None:
            raise Refused(f"{key} has no address to remove")
        if not old.known:
            raise Refused(f"the entry for {key} was {NEWER}", "upgrade moxi-tunnel")
        url = api.url(old.host, old.port)
        core = old.provider == "cloudflare"
        data = dict(rec=rec, key=key, old=old)
        if record.names_pending(old):
            lines = [f"{key}: undo the replace on port {old.port} that has not finished; "
                     "nothing of it is live"]
            return _Plan(lines, ("undo", key, tuple(lines)), core, data=dict(data, kind="undo"))
        if forget:
            lines = [f"{key}: forget {url}. Tunnel drops it from its record and leaves the "
                     "live config as it is."]
            if old.provider == "tailscale":
                lines.append(f"Remove it by hand later with: {_hand_off(old)}")
            return _Plan(lines, ("forget", key, tuple(lines)), core,
                         data=dict(data, kind="forget"))
        prov = provider.get(old.provider)
        serve = prov.serve_config()
        gone = not _hostport_present(serve, old)
        if not gone and not ts_.matches(serve, old):
            raise Refused(f"{key}'s live config on port {old.port} no longer matches what "
                          f"Moxi applied: {ts_.describe(ts_.parts(serve, old.port))}",
                          f"remove it by hand with {_hand_off(old)}, then moxi tunnel "
                          f"remove {key} --forget; or keep it with moxi tunnel remove "
                          f"{key} --forget")
        lines = [f"{key}: remove {url}" + (" (its live config is already gone)" if gone
                                           else ""), "Paired devices lose this address."]
        return _Plan(lines, ("remove", key, gone, tuple(lines)), core,
                     data=dict(data, kind="remove", ts=prov, serve=serve, gone=gone))

    return _run(plan, _do_remove, ask)


def _do_remove(plan: _Plan, act: _Act) -> Outcome:
    d = plan.data
    key, old, kind = d["key"], d["old"], d["kind"]
    url = api.url(old.host, old.port)
    if kind == "undo":
        act.commit({key: None})
        try:
            got = _settle(old.displaced, provider.get("tailscale"), act, finishing=False)
        except TunnelError as exc:
            got = f"its copy stays pending until moxi tunnel repair: {exc.reason}"
        record.event("remove", key, provider=old.provider, port=old.port, undo=got)
        return Outcome(None, [f"{key}: the replace is undone ({got})"])
    if kind == "forget":
        act.commit({key: None})
        record.event("forget", key, provider=old.provider, port=old.port, old_host=old.host)
        notes = [f"{key}: forgotten; {url} stays live"]
        if old.provider == "tailscale":
            notes.append(f"remove it by hand with: {_hand_off(old)}")
        return Outcome(None, notes)
    ts = d["ts"]
    if not d["gone"]:
        act.commit({key: replace(old, state=State.REMOVING)})
        _step("remove-removing")
        try:
            ts.remove(old)
            _step("remove-removed")
            now = ts.serve_config(fresh=True)
            if _hostport_present(now, old):
                raise ApplyError(f"after the removal, port {old.port} still holds "
                                 f"{ts_.describe(ts_.parts(now, old.port))}")
        except Exception as exc:  # noqa: BLE001 - every failure goes back or is kept
            _fail(act, ts, exc, key=key, ports_=[old.port], before_serve=d["serve"],
                  restore=old, kind="remove")
    act.commit({key: None})
    record.event("remove", key, provider=old.provider, port=old.port, old_host=old.host)
    return Outcome(None, [f"{key}: {url} is removed"])


# -- settling a pending copy --------------------------------------------------------------

def _settle(name: str, ts: ts_.Tailscale, act: _Act, *, finishing: bool) -> str:
    """Settle the pending copy `name` on a fresh read of its port, under `tunnel.lock`.

    1. The port holds the copy as written and nothing beside it. An `applying` entry that
       names the file, when `repair` is `finishing`, finishes the replace: the copy's CLI
       form removes it, then 2. A hand edit made between that read and the removal is lost
       (the CLI has no compare-and-set; the copy holds what the user confirmed). A
       `removing` entry that names it is an undo that has committed: it is dropped and the
       file deleted. With no entry naming it, the file is deleted.
    2. The port holds nothing of the copy, or only Moxi's own entry matching the record:
       the file takes its final name. An `applying` entry goes on as a half add; a
       `removing` one is dropped (undone after the removal ran).
    3. Anything else: tunnel touches neither the live config nor the file, and a naming
       entry stays with `detail` saying the port no longer holds what was confirmed.

    Returns what happened: `deleted`, `renamed`, `finished`, `undone`, `held` or `failed`.
    """
    copy = record.read_copy(name)
    port = int(copy["port"])
    serve = ts.serve_config(fresh=True)
    now = ts_.parts(serve, port)
    rec = record.read()
    naming = [(k, e) for k, e in rec.entries.items() if e.known and e.displaced == name]
    key, entry = naming[0] if naming else (None, None)
    if now == copy["parts"]:
        if entry is None:
            record.delete_pending(name)
            return "deleted"
        if entry.state == State.REMOVING:
            act.commit({key: None})
            record.delete_pending(name)
            return "undone"
        if not finishing:
            return _hold(act, key, entry, port)
        try:
            ts.remove_copy(copy)
            serve = ts.serve_config(fresh=True)
        except Exception as exc:  # noqa: BLE001 - the entry keeps the failure
            act.commit({key: replace(entry, detail=_reason(exc))})
            return "failed"
        now = ts_.parts(serve, port)
        if now:
            return _hold(act, key, entry, port)
        record.rename_pending(name)
        record.event("replace", key, provider=ts.name, port=port, copy=name)
        return "finished"
    own = (entry is not None and ts_.matches(serve, entry)
           and ts_.only_ours(serve, entry, hosts=[entry.host], targets=[entry.target]))
    if not now or own:
        record.rename_pending(name)
        if entry is not None and entry.state == State.REMOVING:
            act.commit({key: None})
            return "undone"
        return "renamed"
    if entry is None:
        return "held"
    return _hold(act, key, entry, port)


def _hold(act: _Act, key: str, entry: Entry, port: int) -> str:
    detail = f"port {port} no longer holds what the user confirmed"
    if entry.detail != detail:
        act.commit({key: replace(entry, detail=detail)})
    return "held"


# -- repair -------------------------------------------------------------------------------

def repair(*, ask: Confirm) -> Outcome:
    """Bring every recorded entry back to `ok` where tunnel can: settle every pending copy
    first, then re-apply `missing` and `moved` entries on their port and host, move
    `renamed` ones to the machine's new host on their port, and finish `half` ones. Each
    re-apply uses the target's loopback port now, unless that clashes with another
    entry's. A `changed` Tailscale entry, a `clash`, an `orphan` and a replace held for the
    user are left and listed. Exits 1 when a provider act failed, 3 when something is left
    for the user, else 0."""

    def plan() -> _Plan:
        rec = _writable(record.read())
        decls = declare.read()
        live = _Live()
        states = _states(rec, decls, list(rec.entries), live)
        pending = [d for d in record.listing() if d.pending]
        lines: list[str] = []
        held: list[str] = []
        acts: list[tuple] = []
        ct_hosts: list[str] = []
        if pending or any(s.entry.provider == "tailscale" for s in states):
            ts = live.get("tailscale")
            host_now = ts.host()
            serve = ts.serve_config()
        else:
            ts, host_now, serve = None, None, {}
        for d in pending:
            copy = record.read_copy(d.name)
            naming = [k for k, e in rec.entries.items() if e.displaced == d.name]
            if naming and ts_.parts(serve, d.port) == copy["parts"]:
                lines.append(f"{naming[0]}: finish the replace on port {d.port}")
                lines.append(warn.replace_line(d.port, copy, str(record.final_path(d.name))))
            else:
                lines.append(f"settle the copy {d.name} in {record.displaced_dir()}")
            acts.append(("settle", d.name))
        for s in states:
            e = s.entry
            url = api.url(e.host, e.port)
            if s.check == "half" and e.provider == "tailscale" and record.names_pending(e):
                continue
            moving = (e.provider == "tailscale" and host_now is not None
                      and normal_host(e.host) != host_now
                      and (s.check == "renamed" or (s.check == "half"
                                                    and e.state == State.APPLYING)))
            if moving:
                new_url = api.url(host_now, e.port)
                lines.append(f"{s.target}: move {url} to {new_url}")
                more, ct = warn.add_lines(provider=e.provider, host=host_now, url=new_url,
                                          module=s.target.split("/")[0], reach=e.reach,
                                          exposure=e.exposure, warned=rec.warned_ct)
                lines += [*more, warn.REPAIR_LINE]
                if ct:
                    ct_hosts.append(host_now)
                acts.append(("act", s.target, s.check, host_now))
            elif s.check in ("half", "missing", "moved"):
                lines.append(f"{s.target}: {s.check}, {s.detail}: put {url} right")
                acts.append(("act", s.target, s.check))
            elif s.check != "ok":
                held.append(f"{s.target}: {s.check}: {s.detail}" +
                            (f" (fix: {s.fix})" if s.fix else ""))
        signature = ("repair", tuple(acts), tuple(lines))
        data = dict(rec=rec, decls=decls, ts=ts, ct=ct_hosts, held=held)
        if not acts:
            return _Plan(done=Outcome(None, held or ["nothing to repair"],
                                      codes.REFUSED if held else codes.OK))
        return _Plan(lines, signature, core=True, data=data)

    return _run(plan, _do_repair, ask)


def _do_repair(plan: _Plan, act: _Act) -> Outcome:
    d = plan.data
    ts: ts_.Tailscale | None = d["ts"]
    notes: list[str] = []
    failed = False
    held = list(d["held"])
    if ts is not None:
        for copy in [c for c in record.listing() if c.pending]:
            got = _settle(copy.name, ts, act, finishing=True)
            notes.append(f"the copy {copy.name}: {got}")
            failed |= got == "failed"
            if got == "held":
                held.append(f"the copy {copy.name} on port {copy.port} is held: the port "
                            "no longer holds what the user confirmed (fix: moxi tunnel "
                            "remove <target> undoes the replace; moxi tunnel add <target> "
                            "--replace then asks again)")
    rec = record.read()
    decls = declare.read()
    ports_now = _ports_now(rec, decls)
    clash = declare.clashes(ports_now)
    if ts is not None:
        serve = ts.serve_config(fresh=True)
        host_now = ts.host()
    for key in sorted(rec.entries):
        e = rec.entries[key]
        if not e.known or e.provider != "tailscale" or ts is None:
            continue
        if key not in decls.targets and key not in _bad_keys(decls):
            continue
        if record.names_pending(e):
            continue
        state = _state(key, e, decls, _bad_keys(decls), ports_now, clash,
                       lambda entry: ts_.live_state(serve, entry, host_now))
        if state.check not in ("half", "missing", "moved", "renamed"):
            continue
        target = e.target if key in clash else ports_now.get(key, e.target)
        if e.state == State.REMOVING:
            got = _finish_remove(act, ts, key, e)
        else:
            got = _reapply(act, ts, key, e, host=host_now, target=target,
                           ct=host_now in d["ct"])
        notes.append(f"{key}: {got}")
        failed |= got.startswith("failed")
        if got.startswith("held"):
            held.append(f"{key}: {got}")
        serve = ts.serve_config()
    _cloudflare_step(act)
    code = codes.ERROR if failed else codes.REFUSED if held else codes.OK
    return Outcome(None, notes + held, code)


def _cloudflare_step(act: _Act) -> None:
    """Where repair runs the Cloudflare step last, whether or not it changed a Cloudflare
    entry. This version has no Cloudflare provider, so there is nothing to bring into
    step."""


def _finish_remove(act: _Act, ts: ts_.Tailscale, key: str, e: Entry) -> str:
    serve = ts.serve_config(fresh=True)
    if _hostport_present(serve, e):
        if not ts_.matches(serve, e):
            return (f"held: its live config no longer matches what Moxi applied (fix: "
                    f"{_hand_off(e)}, then moxi tunnel remove {key} --forget)")
        try:
            ts.remove(e)
            if _hostport_present(ts.serve_config(fresh=True), e):
                raise ApplyError(f"port {e.port} still holds it after the removal")
        except Exception as exc:  # noqa: BLE001 - the entry keeps the failure
            act.commit({key: replace(e, detail=_reason(exc))})
            return f"failed: {_reason(exc)}"
    act.commit({key: None})
    record.event("remove", key, provider=e.provider, port=e.port, old_host=e.host)
    return "removed"


def _reapply(act: _Act, ts: ts_.Tailscale, key: str, e: Entry, *, host: str, target: int,
             ct: bool) -> str:
    """Apply `e` again on its port, on `host` with loopback port `target`, and commit it
    `on`. It applies only over a port that holds nothing or Moxi's own config for it, so
    no config Moxi has no copy of is overwritten."""
    serve = ts.serve_config(fresh=True)
    new = replace(e, host=host, target=target, state=State.ON, detail=None,
                  applied_at=record.stamp())
    renamed = normal_host(e.host) != normal_host(host)
    if not ts_.only_ours(serve, e, hosts=[e.host, host], targets=[e.target, target]):
        detail = (f"port {e.port} holds config Moxi did not apply: "
                  f"{ts_.describe(ts_.parts(serve, e.port))}")
        act.commit({key: replace(e, state=State.APPLYING, detail=detail)})
        return (f"held: {detail} (fix: remove that config by hand, or moxi tunnel remove "
                f"{key})")
    warned = [host] if ct and renamed else []
    if not ts_.matches(serve, new):
        if e.state != State.APPLYING:
            act.commit({key: replace(e, state=State.APPLYING)}, warned=warned)
            warned = []
        _step("repair-applying")
        try:
            ts.apply(new)
            serve = ts.serve_config(fresh=True)
            if not ts_.matches(serve, new):
                raise ApplyError(f"after the apply, port {e.port} does not hold what Moxi "
                                 f"applied: {ts_.describe(ts_.parts(serve, e.port))}")
        except Exception as exc:  # noqa: BLE001 - the entry keeps the failure
            cur = act.current(key) or e
            act.commit({key: replace(cur, detail=_reason(exc))})
            return f"failed: {_reason(exc)}"
    act.commit({key: new}, warned=warned)
    if renamed:
        stale = [k for k in ts_.parts(serve, e.port).get("Web", {})
                 if k.rpartition(":")[0] != normal_host(host)]
        record.event("rename", key, provider=e.provider, port=e.port, old_host=e.host,
                     new_host=host, stale=stale)
        line = f"renamed to {api.url(host, e.port)}"
        if stale:
            line += (f"; tailscale still lists {', '.join(stale)} under the old name, "
                     "which no longer reaches this machine")
        return line
    record.event("repair", key, provider=e.provider, port=e.port, loopback=target)
    return f"ok on {api.url(host, e.port)}"


# -- status -------------------------------------------------------------------------------

def status(*, live: bool = False) -> dict:
    """Every declared or recorded target, then the copies in `displaced/`. With `live`,
    each recorded target also gets its check state."""
    rec = record.read()
    decls = declare.read()
    checks = {}
    if live:
        checks = {s.target: s for s in _states(rec, decls, list(rec.entries), _Live())}
    rows = []
    for key in sorted(set(decls.targets) | set(rec.entries)):
        module, _, name = key.partition("/")
        entry = rec.entries.get(key)
        target = decls.targets.get(key)
        row = {"module": module, "name": name,
               "reach": entry.reach if entry else target.reach if target else None,
               "exposure": None, "provider": None, "state": None, "url": None,
               "host": None, "port": None, "target": None, "change": None,
               "displaced": None, "applied_at": None, "detail": None}
        if entry is not None:
            row.update(exposure=entry.exposure if entry.known else None,
                       provider=entry.provider, state=str(entry.state),
                       url=api.url(entry.host, entry.port) if entry.known else None,
                       host=entry.host, port=entry.port, target=entry.target,
                       change=rec.token(key), displaced=entry.displaced,
                       applied_at=entry.applied_at,
                       detail=entry.detail if entry.known else NEWER)
        if live:
            got = checks.get(key)
            row["check"] = got.check if got else None
            if got and got.check != "ok":
                row["check_detail"] = got.detail
                row["fix"] = got.fix
        rows.append(row)
    copies = []
    for d in record.listing():
        naming = [k for k, e in rec.entries.items() if e.displaced == d.name]
        copies.append({"name": d.name, "port": d.port, "pending": d.pending,
                       "path": str(d.path), "target": naming[0] if naming else None,
                       "settles": "moxi tunnel repair, or moxi tunnel remove of its target"
                       if d.pending else None})
    return {"targets": rows, "displaced": copies}


__all__ = ["CHECKS", "EntryState", "Outcome", "check", "add", "remove", "repair", "status"]
