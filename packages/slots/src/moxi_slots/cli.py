"""`moxi slots`: the slots command.

Exit codes are `moxi.exit`'s: 0 done; 1 error (tmux failed, the registry unreadable);
2 usage; 3 refused (occupied, held, in use, missing, name taken, a system slots does not
run on); 75 the lock not had in 10 s or the server not yet answering. On native Windows
every verb says where slots runs and exits 3.

Each verb is added to `VERBS` as `name -> (add, run)`: `add(subparsers)` declares its
parser, `run(args) -> int` runs it. A refusal prints its reason word and the command
that fixes it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Sequence

from moxi import exit as codes

from . import support
from .errors import Refused, RegistryError, TmuxError, TryAgain, UsageError

#: The verbs, in the order `--help` lists them.
VERBS: dict[str, tuple[Callable[[argparse._SubParsersAction], None],
                       Callable[[argparse.Namespace], int]]] = {}


def parser(prog: str) -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(
        prog=prog, description="Numbered tmux sessions to launch into, on one supervised "
                               "tmux server.")
    verbs = top.add_subparsers(dest="verb", metavar="VERB")
    verbs.required = True
    for add, _ in VERBS.values():
        add(verbs)
    return top


def refused(error: Refused) -> int:
    print(f"{error.reason}: {error.words}", file=sys.stderr)
    return codes.REFUSED


def main(argv: Sequence[str] | None = None, *, prog: str = "moxi slots") -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if not support.system_ok():
        print(f"unsupported: {support.unsupported_words()}", file=sys.stderr)
        return codes.REFUSED
    try:
        args = parser(prog).parse_args(args_list)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else codes.USAGE
    _, run = VERBS[args.verb]
    try:
        return run(args)
    except Refused as exc:
        return refused(exc)
    except TryAgain as exc:
        print(f"try again: {exc}", file=sys.stderr)
        return codes.TEMPFAIL
    except UsageError as exc:
        print(f"usage: {exc}", file=sys.stderr)
        return codes.USAGE
    except (TmuxError, RegistryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return codes.ERROR


# -- output ------------------------------------------------------------------------------

def _json(value) -> None:
    print(json.dumps(value, indent=1, sort_keys=True))


def _when(epoch: float | None) -> str:
    if epoch is None:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch))


def _hold_text(slot) -> str:
    if slot.hold is None:
        return "-"
    return f"{slot.hold.holder} until {_when(slot.hold.until)}"


def _row_text(slot) -> list[str]:
    last = "-" if slot.last is None else f"{slot.last.label} at {_when(slot.last.at)}"
    return [str(slot.number), slot.name or "-", slot.state,
            str(slot.attached), _hold_text(slot), slot.label or "-", last]


HEADER = ["SLOT", "NAME", "STATE", "ATTACHED", "HOLD", "LABEL", "LAST LAUNCH"]


def _table(rows: list[list[str]]) -> str:
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(r)).rstrip()
                     for r in rows)


def _number(text: str) -> int | str:
    return int(text) if text.isdigit() else text


# -- verbs -------------------------------------------------------------------------------

def _add_list(verbs) -> None:
    p = verbs.add_parser("list", help="every slot: state, hold, label, attached, last "
                                      "launch; and drift")
    p.add_argument("--json", action="store_true", help="print the listing as JSON")


def _run_list(args) -> int:
    from .slots import list_slots

    listing = list_slots()
    if args.json:
        _json(listing.to_json())
        return codes.OK if listing.ok else codes.ERROR
    if not listing.ok:
        print(f"error: the slots could not be read: {listing.why}", file=sys.stderr)
        return codes.ERROR
    every = [*listing.slots, *listing.unregistered]
    if not every:
        print("no slots; `moxi slots create` makes one")
    else:
        print(_table([HEADER, *(_row_text(s) for s in listing.slots),
                      *([*_row_text(s)[:1], "(no row)", *_row_text(s)[2:]]
                        for s in listing.unregistered)]))
    for drift in listing.drift:
        print(f"drift: {drift.words}")
    return codes.OK


def _add_show(verbs) -> None:
    p = verbs.add_parser("show", help="one slot")
    p.add_argument("slot", metavar="N|NAME")
    p.add_argument("--json", action="store_true", help="print the slot as JSON")


def _run_show(args) -> int:
    from . import panes, registry
    from .slots import resolve

    reg = registry.read()
    number = resolve(_number(args.slot), reg)
    listing = panes.listing(reg)
    found = listing.find(number)
    if found is None:
        raise Refused("no-slot", f"there is no slot {args.slot}; `moxi slots list` shows "
                                 "the slots")
    if args.json:
        _json({**found.to_json(),
               "drift": [d.words for d in listing.drift if d.number == number]})
        return codes.OK
    pairs = [("slot", str(found.number)), ("name", found.name or "-"),
             ("state", found.state), ("launchable", "yes" if found.launchable else "no"),
             ("idle", "yes" if found.idle else "no"), ("attached", str(found.attached)),
             ("hold", _hold_text(found)), ("label", found.label or "-")]
    if found.pane_id:
        pairs.append(("run pane", f"{found.pane_id} in {found.window_id}"))
    if found.current_command:
        pairs.append(("running", f"{found.current_command} (pid {found.pane_pid})"))
    if found.dead_status is not None:
        pairs.append(("exit status", str(found.dead_status)))
    if found.last is not None:
        pairs.append(("last launch", f"{found.last.label} at {_when(found.last.at)}"))
        if found.last.meta:
            pairs.append(("last meta", json.dumps(found.last.meta, sort_keys=True)))
    pairs.append(("created", _when(found.created) if found.created else "(no row)"))
    pairs += [("drift", d.words) for d in listing.drift if d.number == number]
    width = max(len(k) for k, _ in pairs)
    for key, value in pairs:
        print(f"{key.ljust(width)}  {value}")
    return codes.OK


def _add_create(verbs) -> None:
    p = verbs.add_parser("create", help="make slots: the numbers given (a missing one is "
                                        "made again), or the lowest free ones")
    p.add_argument("numbers", metavar="N", nargs="*", type=int)
    p.add_argument("--count", type=int, default=1, metavar="K",
                   help="how many of the lowest free numbers to make (default 1)")
    p.add_argument("--name", help="name the one slot made")


def _run_create(args) -> int:
    from .slots import create

    made = create(args.numbers or None, count=args.count, name=args.name)
    for number in made:
        print(f"slot {number} made; attach once, from the terminal you watch it in: "
              f"moxi slots attach {number}")
    return codes.OK


def _add_remove(verbs) -> None:
    p = verbs.add_parser("remove", help="kill idle slots and drop them from the registry")
    p.add_argument("slots", metavar="N|NAME", nargs="*")
    p.add_argument("--force", action="store_true",
                   help="remove the named slots even when not idle (never an occupied or "
                        "held one)")
    p.add_argument("--all-free", action="store_true",
                   help="remove every free or ended slot that is idle and unheld, and say "
                        "which were skipped")
    p.add_argument("--holder", metavar="H", help="the hold you have on the slot")


def _run_remove(args) -> int:
    from .slots import remove

    if args.all_free and (args.slots or args.force):
        raise UsageError("--all-free takes no slot and no --force")
    if not args.all_free and not args.slots:
        raise UsageError("name the slots to remove, or --all-free")
    done = remove([_number(s) for s in args.slots] or None, all_free=args.all_free,
                  force=args.force, holder=args.holder)
    for number in done.removed:
        print(f"slot {number} removed")
    for number, why in done.skipped:
        print(f"slot {number} skipped: {_SKIPPED.get(why, why)}")
    if args.all_free and not done.removed and not done.skipped:
        print("no slots")
    return codes.OK


_SKIPPED = {"in-use": "not idle (`moxi slots remove N --force` removes it)",
            "held": "held", "occupied": "occupied", "missing": "missing (no session; "
            "`moxi slots remove N` drops it)"}


def _add_rename(verbs) -> None:
    p = verbs.add_parser("rename", help="name a slot, or clear its name")
    p.add_argument("slot", metavar="N|NAME")
    p.add_argument("name", nargs="?", metavar="NAME")
    p.add_argument("--clear", action="store_true", help="clear the slot's name")


def _run_rename(args) -> int:
    from .slots import rename

    if args.clear == (args.name is not None):
        raise UsageError("give a NAME, or --clear")
    rename(_number(args.slot), None if args.clear else args.name)
    print(f"slot {args.slot}: " + ("name cleared" if args.clear else f"named {args.name}"))
    return codes.OK


def _add_attach(verbs) -> None:
    p = verbs.add_parser("attach", help="attach this terminal to a slot (made when it has "
                                        "no session)")
    p.add_argument("slot", metavar="N|NAME")
    p.add_argument("--print", action="store_true",
                   help="print the command instead of running it")


def _run_attach(args) -> int:
    from .slots import attach_argv, attach_env, quoted

    argv = attach_argv(_number(args.slot))
    if args.print:
        print(quoted(argv))
        return codes.OK
    sys.stdout.flush()
    os.execve(argv[0], argv, attach_env(argv))
    return codes.ERROR  # not reached


def _add_hold(verbs) -> None:
    p = verbs.add_parser("hold", help="reserve a slot for a holder, for a while")
    p.add_argument("slot", metavar="N|NAME")
    p.add_argument("--holder", required=True, metavar="H")
    p.add_argument("--for", dest="seconds", required=True, type=float, metavar="S",
                   help="seconds, at most a day")


def _run_hold(args) -> int:
    from .slots import hold

    found = hold(_number(args.slot), args.holder, args.seconds)
    print(f"slot {args.slot} held by {found.holder} until {_when(found.until)}")
    return codes.OK


def _add_release(verbs) -> None:
    p = verbs.add_parser("release", help="end your hold on a slot")
    p.add_argument("slot", metavar="N|NAME")
    p.add_argument("--holder", required=True, metavar="H")


def _run_release(args) -> int:
    from .slots import release

    if release(_number(args.slot), args.holder):
        print(f"slot {args.slot}: hold by {args.holder} ended")
    else:
        print(f"slot {args.slot}: no live hold by {args.holder}")
    return codes.OK


VERBS.update({"list": (_add_list, _run_list), "show": (_add_show, _run_show),
              "create": (_add_create, _run_create), "remove": (_add_remove, _run_remove),
              "rename": (_add_rename, _run_rename), "attach": (_add_attach, _run_attach),
              "hold": (_add_hold, _run_hold), "release": (_add_release, _run_release)})


__all__ = ["VERBS", "parser", "refused", "main"]
