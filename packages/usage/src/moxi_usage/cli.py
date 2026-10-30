"""`moxi usage`: a thin command line over `moxi_usage.api`.

    moxi usage prices [MODEL] [--provider P] [--json]
    moxi usage prices update [--dry-run] [--revert] [--json]
    moxi usage index [--rebuild] [--force] [--no-wait] [--check] [--json]

`prices MODEL` prints the model's facts and where each came from, and exits 1 when the
table does not know the model; `prices` alone prints the layers in effect. `prices update`
fetches LiteLLM's price file and writes the snapshot layer (`moxi_usage.prices`); a fetch
that fails exits 75, a result usage will not use exits 3. With `--json`, each prints one
JSON object.

`index` runs the token index's pass (`moxi_usage.index`) and prints what it did: exit 75
when another pass holds the index past `index.wait_seconds` (at once with `--no-wait`), 3
when the index's schema can't move (`--rebuild --force` drops offline archive lanes'
rows to let it), and with `--check` 1 when the stored claims differ from the `claimed`
view, naming the keys.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from moxi import exit as codes
from moxi.config import ConfigError

#: How many model ids `prices update` names per change before it counts the rest.
SHOWN = 20


def main(argv: Sequence[str] | None = None, *, prog: str = "moxi usage") -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:2] == ["prices", "update"]:
        return _update(_update_parser(prog).parse_args(args[2:]), prog)
    parser = argparse.ArgumentParser(prog=prog, description="Model facts, limits, context "
                                     "and token costs.")
    verbs = parser.add_subparsers(dest="verb", metavar="VERB")
    verbs.required = True
    prices = verbs.add_parser("prices", help="print a model's facts, or the model table "
                              "in effect; `prices update` fetches a new snapshot",
                              description="Print a model's facts and where each came "
                              "from, or the layers of the model table in effect. "
                              "`prices update` fetches LiteLLM's price file.")
    prices.add_argument("model", nargs="?", metavar="MODEL", help="a model id")
    prices.add_argument("--provider", help="the request's provider, for the lookup")
    prices.add_argument("--json", action="store_true", help="print one JSON object")
    _index_parser(verbs)
    parsed = parser.parse_args(args)
    try:
        if parsed.verb == "index":
            return _index(parsed, prog)
        return _prices(parsed)
    except ConfigError as exc:
        print(f"{prog}: {exc}", file=sys.stderr)
        return exc.exit_code


def _update_parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"{prog} prices update",
        description="Fetch LiteLLM's model price file from prices.url and write it as "
                    "the snapshot layer over the shipped model table.")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would change and write nothing")
    parser.add_argument("--revert", action="store_true",
                        help="remove the snapshot layer, so the shipped table rules again")
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    return parser


# --- prices ---------------------------------------------------------------------------

def _prices(args: argparse.Namespace) -> int:
    from . import models

    if args.model is None:
        current = models.table()
        summary = _summary(current)
        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            _print_summary(summary)
        return codes.OK
    facts = models.model_facts(args.model, provider=args.provider)
    if args.json:
        print(json.dumps(facts.as_dict(), indent=2))
    else:
        _print_facts(facts)
    return codes.OK if facts.known else codes.ERROR


def _summary(current: Any) -> dict[str, Any]:
    from . import models

    layers = [{"layer": models.SHIPPED_LAYER, "date": current.dates.get(models.SHIPPED_LAYER),
               "rows": len(current.shipped.get("models", {}))}]
    if current.snapshot is not None:
        layers.append({"layer": models.SNAPSHOT_LAYER,
                       "date": current.dates.get(models.SNAPSHOT_LAYER),
                       "rows": len(current.snapshot.get("models", {})),
                       "path": str(models.snapshot_path())})
    if current.overrides:
        layers.append({"layer": models.OVERRIDE_LAYER, "rows": len(current.overrides),
                       "overrides": [row.as_dict() for row in current.overrides]})
    return {"effective_date": current.effective_date, "rows": len(current.rows),
            "layers": layers, "prefixes": sorted(current.prefixes),
            "variants": sorted(current.variants), "problems": list(current.problems)}


def _print_summary(summary: dict[str, Any]) -> None:
    print(f"model table in effect: {summary['rows']} models, snapshot of "
          f"{summary['effective_date']}")
    for layer in summary["layers"]:
        if layer["layer"] == "override":
            print(f"  override  {layer['rows']} [[model]] rows in usage's config")
            for row in layer["overrides"]:
                print(f"            {json.dumps(row)}")
        else:
            where = f"  {layer['path']}" if "path" in layer else ""
            print(f"  {layer['layer']:<9} {layer['rows']} rows, LiteLLM of "
                  f"{layer['date']}{where}")
    if summary["variants"]:
        print(f"  variants  {', '.join(summary['variants'])}")
    for problem in summary["problems"]:
        print(f"  skipped: {problem}")


def _money(value: Any) -> str:
    return f"${value:,.6g} per million tokens"


def _print_facts(facts: Any) -> None:
    data = facts.as_dict()
    provenance = data["provenance"]
    if not facts.known:
        print(f"{facts.model}: unknown. The model table has no row for it, so its tokens "
              "count as unpriced, never as $0; add a [[model]] row to usage's config to "
              "price it.")
        return
    where = [provenance["rule"]]
    if provenance.get("layer"):
        where.append(f"{provenance['layer']} layer")
    if provenance.get("snapshot"):
        where.append(f"snapshot of {provenance['snapshot']}")
    if provenance.get("row") and provenance["row"] != facts.model:
        where.append(f"row {provenance['row']}")
    if provenance.get("variant"):
        where.append(f"variant {provenance['variant']}")
    print(f"{facts.model}  ({', '.join(where)})")
    filled = set(provenance.get("filled", ()))
    overridden = set(provenance.get("overridden", ()))
    for name in ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h"):
        if name in data:
            how = (" (provider default)" if name in filled
                   else " (your config)" if name in overridden else "")
            print(f"  {name:<15} {_money(data[name])}{how}")
    for name in ("window", "max_output"):
        if name in data:
            how = " (your config)" if name in overridden else ""
            print(f"  {name:<15} {data[name]:,} tokens{how}")
    if data.get("provider"):
        print(f"  {'provider':<15} {data['provider']}")
    for tier, values in (data.get("tiers") or {}).items():
        parts = ", ".join(f"{k} {v}" for k, v in values.items())
        print(f"  tier {tier:<10} {parts}")
    if provenance.get("note"):
        print(f"  note            {provenance['note']}")
    if provenance.get("period"):
        print(f"  period          {json.dumps(provenance['period'])}")
    if data.get("unknown"):
        print(f"  unknown         {', '.join(data['unknown'])}")


# --- index ----------------------------------------------------------------------------

#: How many keys `index --check` names before it counts the rest.
KEYS_SHOWN = 20


def _index_parser(verbs: Any) -> None:
    index = verbs.add_parser(
        "index", help="read what changed in the harnesses' records into the token index",
        description="Run the token index's pass: read every source that changed since the "
                    "last pass, and print what it did.")
    index.add_argument("--rebuild", action="store_true",
                       help="read every source again from its start")
    index.add_argument("--force", action="store_true",
                       help="let a schema change drop the rows of offline archive lanes")
    index.add_argument("--no-wait", action="store_true",
                       help="exit 75 at once when another pass holds the index")
    index.add_argument("--check", action="store_true",
                       help="after the pass, compare the stored claims with the claimed "
                            "view; exit 1 naming the keys that differ")
    index.add_argument("--json", action="store_true", help="print one JSON object")


def _index(args: argparse.Namespace, prog: str) -> int:
    import moxi.files
    import moxi.store

    from . import index, store

    try:
        result = index.pass_(rebuild=args.rebuild, force=args.force,
                             wait=0.0 if args.no_wait else None)
    except moxi.files.LockTimeout as exc:
        print(f"{prog} index: another pass holds the index: {exc}", file=sys.stderr)
        return codes.TEMPFAIL
    except moxi.store.StoreTooNew as exc:
        print(f"{prog} index: {exc}", file=sys.stderr)
        return codes.REFUSED
    except store.MigrationRefused as exc:
        print(f"{prog} index: refused: {exc}", file=sys.stderr)
        return exc.exit_code
    differ: list[int] = []
    if args.check:
        conn = store.connect()
        try:
            differ = store.check(conn)
        finally:
            conn.close()
    doc = result.as_dict()
    if args.check:
        doc["check"] = {"agree": not differ, "keys": differ}
    if args.json:
        print(json.dumps(doc, indent=2))
    else:
        _print_pass(result)
        if args.check:
            _print_check(differ)
    return codes.ERROR if differ else codes.OK


def _print_pass(result: Any) -> None:
    what = "rebuilt" if result.rebuild else "pass"
    print(f"{what}: {result.listed} sources listed, {result.read} read, "
          f"{result.reused} unchanged, {result.excluded} imported, {result.deleted} "
          f"gone, {result.held} held")
    for lane, state in result.lanes.items():
        line = f"  {lane:<16} {state['state']:<8} {state['listed']} listed"
        if state["held"]:
            line += f", {state['held']} held"
        if state.get("imported"):
            line += f", {state['imported']} imported"
        if state.get("compressed"):
            line += f", {state['compressed']} compressed"
        print(line)
        if state.get("reason"):
            print(f"  {'':<16} {state['reason']}")
    for lane in result.dropped_lanes:
        print(f"  dropped {lane}: its archive root left the config")
    if result.migration_dropped:
        print(f"  dropped offline lanes to move the schema: "
              f"{', '.join(result.migration_dropped)}")
    for line in result.unreadable[:KEYS_SHOWN]:
        print(f"  unreadable: {line}")
    if result.readings_error:
        print(f"  limits readings not stored: {result.readings_error}")
    if result.cancelled:
        print("  cancelled: what was read so far is kept")


def _print_check(differ: list[int]) -> None:
    if not differ:
        print("check: the stored claims agree with the claimed view")
        return
    shown = ", ".join(str(key) for key in differ[:KEYS_SHOWN])
    more = f" and {len(differ) - KEYS_SHOWN} more" if len(differ) > KEYS_SHOWN else ""
    print(f"check: {len(differ)} keys differ from the claimed view: {shown}{more}")


# --- prices update ----------------------------------------------------------------------

def _update(args: argparse.Namespace, prog: str, *, fetcher: Any = None,
            now: float | None = None) -> int:
    from . import prices

    try:
        if args.revert:
            path = prices.revert(dry_run=args.dry_run)
            if args.json:
                print(json.dumps({"reverted": path is not None and not args.dry_run,
                                  "path": None if path is None else str(path)}))
            elif path is None:
                print("no snapshot layer; the shipped table already rules")
            else:
                verb = "would remove" if args.dry_run else "removed"
                print(f"{verb} {path}; the shipped table rules")
            return codes.OK
        result = prices.update(fetcher=fetcher, now=now, dry_run=args.dry_run)
    except prices.FetchError as exc:
        print(f"{prog} prices update: {exc}", file=sys.stderr)
        return codes.TEMPFAIL
    except prices.Refused as exc:
        print(f"{prog} prices update: refused: {exc}", file=sys.stderr)
        return codes.REFUSED
    except ConfigError as exc:
        print(f"{prog} prices update: {exc}", file=sys.stderr)
        return exc.exit_code
    if args.json:
        print(json.dumps(result.as_dict(), indent=2))
        return codes.OK
    print(f"LiteLLM of {result.snapshot['date']}: {result.rows} models "
          f"(the shipped table has {result.shipped_rows})")
    for name, ids in (("added", result.added), ("removed", result.removed),
                      ("repriced", result.repriced)):
        shown = ", ".join(ids[:SHOWN])
        more = f" and {len(ids) - SHOWN} more" if len(ids) > SHOWN else ""
        print(f"  {name:<9} {len(ids)}{': ' if ids else ''}{shown}{more}")
    if result.written:
        print(f"wrote {result.path}")
    else:
        print("dry run: nothing written")
    return codes.OK
