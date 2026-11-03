"""tunnel's part in an uninstall: `TEARDOWN` for its own, `on_uninstall` for every module's.

`moxi uninstall tunnel` runs `teardown()` first, in a child process that holds the
uninstall's claim on the core lock. **When it runs, uninstall removes every address tunnel
applied, or stops, naming each one it could not remove.** A Funnel left behind would go on
forwarding public traffic to a loopback port after tunnel and its doctor are gone, so the
user is told every time tunnel can tell.

- It reads the record and the live config under its locks first. When an address can't be
  removed (its live config no longer matches what tunnel applied, tailscale can't be
  read, the entry was written by a newer tunnel, or the record can't be written), it
  raises `moxi.modules.Refused` before it changes anything, naming each address with the
  command that removes it by hand and `moxi tunnel remove <target> --forget` for one the
  user keeps, and saying that `moxi uninstall tunnel --skip-teardown` goes past it and
  leaves them live. The uninstall then stops with the record, the Cloudflare switch,
  `cloudflared.yml` and the doctor in place.
- Otherwise it commits every entry `removing` in one write. That write empties the
  Cloudflare set, so the Cloudflare step turns the daemon switch off and then deletes
  `cloudflared.yml`; a unit act that fails there doesn't stop it, since the uninstall
  removes the unit next. It then removes each live Tailscale entry, only while it still
  matches the record, and drops what it removed in one more write. A removal that fails
  leaves its entry `removing` with the provider's text, and it refuses naming it. When
  no Cloudflare entry was recorded, so no write ran the step, it still runs the step once
  if the switch, the unit or a `cloudflared.yml` left behind disagree with the empty set.
- A Cloudflare entry whose switch from Tailscale has not finished may still have its old
  Tailscale entry live, the one its `from` names. That one counts as a Tailscale entry
  here: it is removed by the same rules, or named with its hand command.
- An entry that names a replace's pending copy has nothing live: the replace is undone
  and the entry counts as removed. An entry recorded under a host the machine no longer
  has is dropped, and any key tailscale still lists under that old name is named, since
  the old name no longer reaches this machine.

`on_uninstall(module)` runs for every module's uninstall, after that module's teardown:

- for another module, it removes that module's addresses without asking, by the same
  rules, and can't refuse: an address it could not remove stays in the record, where the
  doctor shows it as an orphan once its module is gone, and it prints one line for each.
  When `cloudflared.yml` can't be written while other Cloudflare entries stay, the
  module's Cloudflare entries go back as they were, since the file still serves them;
- for tunnel itself it removes nothing and prints one line for each address still in the
  record, with the command that removes it by hand. After a teardown that accepted there
  is none; after `--skip-teardown`, this is where what stays live is named.

Both wait `locks.WAIT` seconds for `tunnel.lock`, and call the change listeners once the
locks are let go.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace

from moxi import files, modules

from . import acts, api, changes, cloudflare as cf_, locks, record
from . import tailscale as ts_
from .cloudflare import StepFailed
from .errors import TunnelError
from .record import NEWER, Entry, Record, State, normal_host

MODULE = "tunnel"
SKIP = "moxi uninstall tunnel --skip-teardown"


@dataclass
class _Plan:
    """What taking a set of entries out would do, read under the locks."""

    #: entries to commit `removing`, then drop
    drop: dict[str, Entry] = field(default_factory=dict)
    #: of those, each one whose Tailscale entry is live, with that entry: its own, or for
    #: a switch from Tailscale that has not settled, the old one its `from` names
    remove: dict[str, Entry] = field(default_factory=dict)
    #: entries that can't be taken out, each with why and what to run by hand
    left: dict[str, tuple[str, str]] = field(default_factory=dict)
    #: keys tailscale still lists under a name the machine no longer has
    stale: list[str] = field(default_factory=list)


def _hand(entry: Entry) -> str:
    return f"tailscale {ts_.verb(entry.reach)} --https={entry.port} off"


def _plan(rec: Record, keys: Iterable[str], ts: ts_.Tailscale) -> _Plan:
    plan = _Plan()
    live: tuple[dict, str] | None = None
    unread: str | None = None
    for key in sorted(keys):
        entry = rec.entries[key]
        if not entry.known:
            plan.left[key] = (f"it was {NEWER}, which this version can't remove",
                              "upgrade moxi-tunnel, or see what it left with: tailscale "
                              "serve status")
            continue
        if record.names_pending(entry):
            plan.drop[key] = entry
            continue
        # A Cloudflare entry whose switch from Tailscale has not settled may still have
        # its old Tailscale entry live: that one is removed by the same rules.
        ts_entry = entry if entry.provider == "tailscale" else record.old_tailscale(entry)
        if ts_entry is None:
            plan.drop[key] = entry
            continue
        what = "" if ts_entry is entry else _old_part(ts_entry)
        if live is None and unread is None:
            try:
                live = (ts.serve_config(), ts.host())
            except TunnelError as exc:
                unread = exc.reason
        if live is None:
            plan.left[key] = (f"{what}tailscale can't be read: {unread}",
                              f"remove it by hand with: {_hand(ts_entry)}")
            continue
        serve, host = live
        hostport = f"{normal_host(ts_entry.host)}:{ts_entry.port}"
        if normal_host(ts_entry.host) != host:
            plan.drop[key] = entry
            plan.stale += [k for k in ts_.parts(serve, ts_entry.port).get("Web", {})
                           if k.rpartition(":")[0] != host]
            continue
        held = ts_.parts(serve, ts_entry.port)
        if hostport not in held.get("Web", {}) and hostport not in held.get("AllowFunnel", {}):
            plan.drop[key] = entry
            continue
        if not ts_.matches(serve, ts_entry):
            plan.left[key] = (f"{what}port {ts_entry.port} no longer holds what Moxi "
                              f"applied: {ts_.describe(held)}",
                              f"remove it by hand with: {_hand(ts_entry)}")
            continue
        plan.drop[key] = entry
        plan.remove[key] = ts_entry
    return plan


def _old_part(old: Entry) -> str:
    """The words that say a reason is about the old Tailscale entry of a switch that has
    not settled."""
    return (f"its switch from Tailscale has not finished, so its old Tailscale entry "
            f"{api.url(old.host, old.port)} may still be live, and ")


@dataclass
class _Done:
    lines: list[str] = field(default_factory=list)
    #: what could not be removed, by target: (why, what to run by hand)
    left: dict[str, tuple[str, str]] = field(default_factory=dict)


def _take_out(act: acts._Act, plan: _Plan, ts: ts_.Tailscale, *, by: str) -> _Done:
    """Commit every entry of `plan.drop` `removing` in one write, remove each live
    Tailscale entry, then drop what went in one more write and settle the copies the
    undone replaces wrote."""
    done = _Done()
    if not plan.drop:
        return done
    try:
        act.commit({k: replace(e, state=State.REMOVING) for k, e in plan.drop.items()})
    except StepFailed as exc:
        if exc.file and act.last is not None and act.last.cloudflare_set():
            # cloudflared.yml could not be written, so it still serves these hostnames
            # for the Cloudflare entries left: they go back, as a plain remove's would.
            _put_back(act, plan, done, exc)
        else:
            done.lines.append(f"tunnel: cloudflared did not follow: {exc.reason}"
                              + (f" (fix: {exc.fix})" if exc.fix and exc.file else ""))
    acts._step("uninstall-removing")
    kept: dict[str, Entry] = {}
    for key, entry in plan.remove.items():
        try:
            ts.remove(entry)
            now = ts.serve_config(fresh=True)
            if acts._hostport_present(now, entry):
                raise TunnelError(f"port {entry.port} still holds it after the removal")
        except TunnelError as exc:
            cur = act.current(key) or entry
            kept[key] = replace(cur, detail=exc.reason)
            done.left[key] = (f"tailscale did not remove it: {exc.reason}",
                              f"remove it by hand with: {_hand(entry)}")
    gone = {k: None for k in plan.drop if k not in kept}
    try:
        act.commit({**gone, **kept})
    except StepFailed as exc:
        done.lines.append(f"tunnel: cloudflared did not follow: {exc.reason}")
    for key in gone:
        entry = plan.drop[key]
        record.event("remove", key, provider=entry.provider, port=entry.port,
                     old_host=entry.host, by=by)
        line = f"tunnel: removed {key} ({api.url(entry.host, entry.port)}"
        old = plan.remove.get(key)
        if old is not None and old.provider != entry.provider:
            line += f", and its old Tailscale entry {api.url(old.host, old.port)}"
        done.lines.append(line + ")")
    for key, entry in plan.drop.items():
        if key in gone and record.names_pending(entry):
            try:
                got = acts._settle(entry.displaced, ts, act, finishing=False)
            except TunnelError as exc:
                got = f"its copy stays pending in {record.displaced_dir()}: {exc.reason}"
            done.lines.append(f"tunnel: {key}: the replace that had not finished is "
                              f"undone ({got})")
    for key in plan.stale:
        done.lines.append(f"tunnel: tailscale still lists {key} under a name this machine "
                          "no longer has; it no longer reaches this machine")
    return done


def _put_back(act: acts._Act, plan: _Plan, done: _Done, exc: StepFailed) -> None:
    """Take the Cloudflare entries out of `plan` and commit them back as they were, since
    the step could not write their rules out of `cloudflared.yml`; name each in
    `done.left`. One that was `on` goes back `applying`, then `on` once the step agrees."""
    back = {k: e for k, e in plan.drop.items()
            if e.provider == "cloudflare" and not record.names_pending(e)}
    for key in back:
        del plan.drop[key]
        plan.remove.pop(key, None)
        done.left[key] = (f"cloudflared.yml can't be written, so it still serves it: "
                          f"{exc.reason}", exc.fix or "moxi tunnel repair")
    going = {k: replace(e, state=State.APPLYING) if e.state == State.ON else e
             for k, e in back.items()}
    try:
        act.commit(going)
        act.commit(back)
    except StepFailed as again:
        act.commit({k: replace(e, detail=again.reason) for k, e in going.items()})
    for key, entry in back.items():
        record.event("rollback", key, provider=entry.provider, port=entry.port,
                     act="uninstall")


def _refusal(left: Mapping[str, tuple[str, str]], rec: Record | None) -> str:
    count = len(left)
    lines = [f"tunnel could not remove {count} "
             f"{'address' if count == 1 else 'addresses'}, so nothing more was removed "
             "and tunnel stays installed:"]
    for key, (why, hand) in sorted(left.items()):
        entry = rec.entries.get(key) if rec is not None else None
        keep = (f"; or keep it and drop it from tunnel's record with: moxi tunnel "
                f"remove {key} --forget") if entry is not None and entry.known else ""
        lines.append(f"  {key}: {why}. {hand[0].upper()}{hand[1:]}{keep}")
    lines.append(f"To uninstall tunnel anyway and leave {'it' if count == 1 else 'them'} "
                 f"live: {SKIP}")
    return "\n".join(lines)


def teardown() -> None:
    """tunnel's `TEARDOWN`: remove every address tunnel applied, or raise
    `moxi.modules.Refused` naming each one it could not remove."""
    before: Record | None = None
    act: acts._Act | None = None
    done = _Done()
    try:
        with locks.held(core=True, timeout=locks.WAIT):
            try:
                rec = record.read()
            except TunnelError as exc:
                raise modules.Refused(f"{exc.reason}. To uninstall tunnel anyway: {SKIP}")
            if rec.exists and not rec.writable:
                raise modules.Refused(
                    f"tunnel's record {record.record_path()} has format {rec.format!r}, "
                    f"which this version doesn't write, so it can't remove what that "
                    f"record holds. Upgrade moxi-tunnel and uninstall again, or see what "
                    f"stays with: tailscale serve status. To uninstall tunnel anyway: "
                    f"{SKIP}")
            ts = ts_.Tailscale()
            plan = _plan(rec, rec.entries, ts)
            if plan.left:
                raise modules.Refused(_refusal(plan.left, rec))
            before = rec
            act = acts._Act(rec)
            done = _take_out(act, plan, ts, by="teardown")
            if not rec.cloudflare_set() and cf_.agreement(act.last or rec)[0] != "ok":
                # No commit changed the Cloudflare set, so no step ran; a switch left on
                # or a file left behind still goes, else a reinstall puts the unit back.
                try:
                    cf_.step(act.last or rec)
                except StepFailed as exc:
                    done.lines.append(f"tunnel: cloudflared did not follow: {exc.reason}")
    except files.LockTimeout as exc:
        raise modules.Refused(f"tunnel's lock is held by process {exc.holder}, so its "
                              f"teardown could not run; wait for that process and uninstall "
                              f"again. To uninstall tunnel anyway: {SKIP}") from None
    finally:
        if act is not None and act.last is not None and before is not None:
            changes.notify(before, act.last)
    for line in done.lines:
        print(line)
    if done.left:
        raise modules.Refused(_refusal(done.left, act.last if act else None))


def on_uninstall(module: str) -> None:
    """Called for every module's uninstall: remove `module`'s addresses, or for tunnel
    itself name every address still in the record."""
    if module == MODULE:
        _name_what_stays()
        return
    try:
        rec = record.read()
    except TunnelError as exc:
        print(f"tunnel: {module}'s addresses, if it has any, were not removed: "
              f"{exc.reason}")
        return
    if not any(k.partition("/")[0] == module for k in rec.entries):
        return
    before: Record | None = None
    act: acts._Act | None = None
    done = _Done()
    left: dict[str, tuple[str, str]] = {}
    try:
        with locks.held(core=True, timeout=locks.WAIT):
            rec = record.read()
            keys = [k for k in rec.entries if k.partition("/")[0] == module]
            if rec.exists and not rec.writable:
                left = {k: (f"tunnel's record has format {rec.format!r}, which this "
                            "version doesn't write", "upgrade moxi-tunnel") for k in keys}
            else:
                ts = ts_.Tailscale()
                plan = _plan(rec, keys, ts)
                left = dict(plan.left)
                before = rec
                act = acts._Act(rec)
                done = _take_out(act, plan, ts, by="uninstall")
                left.update(done.left)
    except files.LockTimeout as exc:
        left = {k: (f"tunnel's lock is held by process {exc.holder}",
                    "moxi tunnel remove " + k) for k in rec.entries
                if k.partition("/")[0] == module}
    finally:
        if act is not None and act.last is not None and before is not None:
            changes.notify(before, act.last)
    for line in done.lines:
        print(line)
    for key, (why, hand) in sorted(left.items()):
        print(f"tunnel: {key} stays in tunnel's record and may stay live: {why}. Once "
              f"{module} is gone, remove it with: moxi tunnel remove {key}; or by hand: "
              f"{hand}")


#: What a purge of store `applied` takes with it besides the addresses.
RETIRED_PURGE_LINE = (
    "tunnel: its record also holds the retired origins, each port or hostname a mapping "
    "to a loopback port once served, which no new address takes; removing store applied "
    "with --purge forgets them, and only the UI's gateway keeps a copy, of its own")


def _name_what_stays() -> None:
    """tunnel's own `on_uninstall`: one line for each address still in the record."""
    try:
        rec = record.read()
    except TunnelError as exc:
        print(f"tunnel: its record can't be read ({exc.reason}), so addresses it applied "
              "may stay live; see what stays with: tailscale serve status")
        return
    for key in sorted(rec.entries):
        entry = rec.entries[key]
        if not entry.known:
            print(f"tunnel: {key} was {NEWER} and may stay live; see what it left with: "
                  "tailscale serve status")
        elif entry.provider == "tailscale":
            print(f"tunnel: {key} stays live at {api.url(entry.host, entry.port)}; remove "
                  f"it by hand with: {_hand(entry)}")
        else:
            print(f"tunnel: {key} ({api.url(entry.host, entry.port)}) stays in tunnel's "
                  "record; the cloudflared unit goes with tunnel, so Cloudflare stops "
                  "serving it, and the tunnel and its DNS route stay in your Cloudflare "
                  "account")
            old = record.old_tailscale(entry)
            if old is not None:
                print(f"tunnel: {key}'s switch from Tailscale had not finished, so its old "
                      f"Tailscale entry may stay live at {api.url(old.host, old.port)}; "
                      f"remove it by hand with: {_hand(old)}")
    if rec.entries:
        print("tunnel: its record is kept, so reinstalling moxi-tunnel takes these "
              "addresses back as they are")
    if rec.retired:
        print(RETIRED_PURGE_LINE)


__all__ = ["teardown", "on_uninstall"]
