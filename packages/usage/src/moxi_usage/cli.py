"""`moxi usage`: a thin command line over `moxi_usage.api`.

    moxi usage prices [MODEL] [--provider P] [--json]
    moxi usage prices update [--dry-run] [--revert] [--json]
    moxi usage limits [--harness H] [--no-pull] [--json]
    moxi usage context SESSION [--harness H] [--agent ID] [--json]
    moxi usage restart-point [--measure] [--json]
    moxi usage marks [SESSION] [--harness H] [--json]
    moxi usage daily|monthly|sessions|models|projects [--since DATE] [--until DATE]
        [--harness H] [--project P] [--model M] [--utc] [--no-pass] [--json]
    moxi usage terms [--by model|day|month|agent] [--session ID] [--since DATE]
        [--until DATE] [--harness H] [--project P] [--model M] [--utc] [--no-pass] [--json]
    moxi usage requests SESSION [--agent ID] [--since DATE] [--until DATE] [--harness H]
        [--project P] [--model M] [--utc] [--no-pass] [--json]
    moxi usage index [--rebuild] [--force] [--no-wait] [--check] [--json]

`prices MODEL` prints the model's facts and where each came from, and exits 1 when the
table does not know the model; `prices` alone prints the layers in effect. `prices update`
fetches LiteLLM's price file and writes the snapshot layer (`moxi_usage.prices`); a fetch
that fails exits 75, a result usage will not use exits 3. With `--json`, each prints one
JSON object.

`limits` prints one line per harness: each window's percent used, when it resets and,
when it is filling, when it reaches 100% at the recent rate, then the age of the oldest
reading on the line. A window it cannot state says why. It exits 0 whatever the states.

`context SESSION` prints how full the session's context is, or with `--agent` one of its
agents', with its denominator: `context 412,330 / 1,000,000 (41%)`. It exits 1 when the
session, the agent or a request with its input tokens cannot be found, and 2 with no
SESSION.

`restart-point` prints the measured restart point per model and population and each
warn-at mark's threshold in tokens, or why a mark is unavailable; it exits 1 when there
is no measure to read. `--measure` runs the index's pass and the measure first and writes
it (`moxi_usage.measure`).

`marks SESSION` prints the session's warn-at marks: its context, the notice's and the
crossing's mark with its threshold and state (armed, or fired and when), and its agents'
fired notices; it exits 1 when the session's context cannot be read. `marks` alone prints
each session's fired marks kept on disk, and each agent the worker ceiling has stopped.

The report verbs run the index's pass first, unless `--no-pass`, and print tokens and cost
from the index (`moxi_usage.reports`). Days are local time, or UTC with `--utc`; `--since`
and `--until` are dates (`YYYY-MM-DD`), both included. A cost the model table cannot give
is never printed as $0: the row is marked and the tokens are counted as unpriced.

`terms` prints every term of each cost: per price group, each token class's tokens, the
price it paid and the dollars, adding up to the group's cost; then the total and the cost
split by class and price tier. `--by` groups by model (the default), day, month or, with
`--session`, the session's agents. A group it can't price prints its tokens, the prices
it has, and why it is unpriced.

`requests SESSION` prints one line per request of the session's main agent, or with
`--agent` one of its agents: its number, time, turn, model, context and percent of the
window, its tokens by class and its cost, with a line where a compaction falls; first a
header with the agent's requests, turns, compactions and window. It exits 1 when no
request of that session and agent is in range, and 2 for an argument it can't use.

`index` runs the token index's pass (`moxi_usage.index`), prints what it did in one line,
then the index's `status` report, lane by lane (with `--json`, both). It exits 75 when
another pass holds the index past `index.wait_seconds` (at once with `--no-wait`), 3 when
the index's schema can't move (`--rebuild --force` drops offline archive lanes' rows to
let it), and with `--check` 1 when the stored claims differ from the `claimed` view,
naming the keys.
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
    limits = verbs.add_parser("limits", help="print how much of each plan window is used",
                              description="Print each plan window's percent used, when "
                              "it resets and when it fills at the recent rate, from the "
                              "readings kept in the last days.")
    limits.add_argument("--harness", choices=("claude", "codex"),
                        help="only this harness's windows")
    limits.add_argument("--no-pull", dest="pull", action="store_false",
                        help="never ask Codex for a new reading, even when the newest is "
                             "stale")
    limits.add_argument("--json", action="store_true", help="print one JSON object")
    _context_parser(verbs)
    _restart_parser(verbs)
    _marks_parser(verbs)
    _report_verbs(verbs)
    _index_parser(verbs)
    parsed = parser.parse_args(args)
    try:
        if parsed.verb == "limits":
            return _limits(parsed, prog)
        if parsed.verb == "context":
            return _context(parsed, prog)
        if parsed.verb == "restart-point":
            return _restart_point(parsed, prog)
        if parsed.verb == "marks":
            return _marks_verb(parsed)
        if parsed.verb in REPORT_VERBS or parsed.verb in ("terms", "requests"):
            return _report(parsed, prog)
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


# --- prices update --------------------------------------------------------------------

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
    for name, ids in (("added", result.added), ("repriced", result.repriced),
                      ("no longer in LiteLLM", result.removed)):
        shown = ", ".join(ids[:SHOWN])
        more = f" and {len(ids) - SHOWN} more" if len(ids) > SHOWN else ""
        print(f"  {name:<9} {len(ids)}{': ' if ids else ''}{shown}{more}")
    if result.dropped:
        shown = ", ".join(result.dropped[:SHOWN])
        more = f" and {len(result.dropped) - SHOWN} more" if len(result.dropped) > SHOWN else ""
        print(f"  supplement rows left out, their models no longer in LiteLLM: "
              f"{len(result.dropped)}: {shown}{more}")
    _print_in_index(result.in_index)
    if result.written:
        print(f"wrote {result.path}")
    else:
        print("dry run: nothing written")
    return codes.OK


def _print_in_index(found: Any) -> None:
    if found is None:
        print("  no token index yet, so no models seen in use to name")
        return
    print(f"  of the {found.models} models in the token index:")
    for name, ids in (("repriced", found.repriced), ("priced", found.priced),
                      ("unpriced", found.unpriced)):
        shown = ", ".join(ids[:SHOWN])
        more = f" and {len(ids) - SHOWN} more" if len(ids) > SHOWN else ""
        print(f"    {name:<9} {len(ids)}{': ' if ids else ''}{shown}{more}")


# --- limits ---------------------------------------------------------------------------

def _limits(args: argparse.Namespace, prog: str, *, now: float | None = None) -> int:
    from moxi.store import StoreTooNew

    from . import limits

    try:
        result = limits.limits(harness=args.harness, now=now, pull=args.pull)
    except StoreTooNew as exc:
        print(f"{prog} limits: {exc}", file=sys.stderr)
        return exc.exit_code
    if args.json:
        print(json.dumps(result.as_dict(), indent=2))
        return codes.OK
    for line in limits_lines(result):
        print(line)
    return codes.OK


def limits_lines(result: Any) -> list[str]:
    """The text of `moxi usage limits`: per harness one line of cells, the oldest age on
    it, then any note under it."""
    from moxi_sessions.api import UNKNOWN

    lines = []
    harnesses = list(dict.fromkeys(row.harness for row in result.rows))
    for harness in harnesses:
        rows = result.of(harness)
        cells = [f"{harness:<7}"] + [_cell(row, result.now) for row in rows]
        line = "   ".join(cells)
        ages = [row.age_seconds for row in rows if row.age_seconds is not UNKNOWN]
        if ages:
            line += f"   as of {format_age(max(ages))} ago"
        lines.append(line)
        for note in dict.fromkeys(row.note for row in rows if row.note):
            lines.append(f"{'':<7}   {note}")
    for note in result.notes:
        lines.append(f"note: {note}")
    return lines


def _cell(row: Any, now: float) -> str:
    # The percent is the percent USED, and the word says so: the harnesses' own apps
    # show what is left, and a bare number reads as that.
    # Of what else the reading said, only a spend limit's dollars are worth the line.
    from .limits import dollars

    shown = dollars(row.extra)
    extra = ""
    if shown:
        extra = " (" + ", ".join(f"{name} ${value:,.2f}" for name, value in shown) + ")"
    if row.state == "fresh":
        text = (f"{row.label} {row.used_percent:.0f}% used, resets "
                f"{format_reset(row.resets_at, now)}")
        if row.full_at is not None:
            text += f", full by {format_reset(row.full_at, now)}"
        return text + extra
    if row.state == "reset":
        return f"{row.label} reset since the last reading{extra}"
    return f"{row.label} unavailable ({row.reason}){extra}"


def format_age(seconds: float) -> str:
    """`40s`, `4m05s`, `1h04m`, `3d04h`, rounded down so a reading never looks fresher
    than it is."""
    seconds = max(int(seconds), 0)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    if seconds < 86400:
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"
    return f"{seconds // 86400}d{(seconds % 86400) // 3600:02d}h"


def format_reset(epoch: Any, now: float) -> str:
    """Local wall time, as coarse as the distance allows: `18:20` today, `Fri 09:00`
    within the week, `Nov 06 09:00` beyond it; `unknown` for None or `UNKNOWN`."""
    from datetime import datetime

    from moxi_sessions.api import UNKNOWN

    if epoch is None or epoch is UNKNOWN:
        return "unknown"
    when = datetime.fromtimestamp(epoch)
    today = datetime.fromtimestamp(now)
    if when.date() == today.date():
        return when.strftime("%H:%M")
    if abs((when - today).days) < 7:
        return when.strftime("%a %H:%M")
    return when.strftime("%b %d %H:%M")


# --- context --------------------------------------------------------------------------

def _context_parser(verbs: Any) -> None:
    context = verbs.add_parser(
        "context", help="print how full a session's context is",
        description="Print how full a session's context is, from its newest request: "
                    "the tokens, the window and the percent used.")
    context.add_argument("session", nargs="?", metavar="SESSION",
                         help="the session id (a Codex thread id, an OpenCode session id)")
    context.add_argument("--harness", choices=("claude", "codex", "opencode"),
                         help="the harness that holds the session; else each is asked")
    context.add_argument("--agent", metavar="ID", help="one of the session's agents")
    context.add_argument("--json", action="store_true", help="print one JSON object")


def _context(args: argparse.Namespace, prog: str) -> int:
    from .context import context

    if not args.session:
        print(f"{prog} context: give a SESSION id; nothing stamped tells usage the "
              "current session", file=sys.stderr)
        return codes.USAGE
    found = context(args.session, harness=args.harness, agent_id=args.agent)
    if args.json:
        print(json.dumps(found.as_dict(), indent=2))
    else:
        print(found.text())
        if found.known and found.reason:
            print(f"note: {found.reason}")
    return codes.OK if found.known else codes.ERROR


# --- marks ------------------------------------------------------------------------------

def _marks_parser(verbs: Any) -> None:
    marks = verbs.add_parser(
        "marks", help="print a session's warn-at marks, or every session's fired marks",
        description="Print a session's warn-at marks: its context, each mark in force "
                    "with its threshold and state, and its agents' fired notices. With "
                    "no SESSION, print the fired marks kept on disk and the agents "
                    "the worker ceiling has stopped.")
    marks.add_argument("session", nargs="?", metavar="SESSION",
                       help="the session id (a Codex thread id, an OpenCode session id)")
    marks.add_argument("--harness", choices=("claude", "codex", "opencode"),
                       help="the harness that holds the session; else each is asked")
    marks.add_argument("--json", action="store_true", help="print one JSON object")


def _marks_verb(args: argparse.Namespace) -> int:
    from . import marks

    if args.session:
        found = marks.mark(args.session, harness=args.harness)
        if args.json:
            print(json.dumps(found.as_dict(), indent=2))
        else:
            for line in found.lines():
                print(line)
        return codes.OK if found.tokens is not marks.UNKNOWN else codes.ERROR
    from .ceiling import latched

    files = marks.fired()
    stopped = latched()
    if args.json:
        print(json.dumps({"fired": [f.as_dict() for f in files],
                          "ceiling": [a.as_dict() for a in stopped]}, indent=2))
        return codes.OK
    if not files:
        print("no fired marks kept")
    for agent in stopped:
        grown = f"grew {agent.growth:,}" if agent.growth is not None else "grew past it"
        when = _local(agent.at) if agent.at is not None else "?"
        print(f"{agent.session_id} agent {agent.agent_id}: past the worker ceiling "
              f"({grown} against {agent.threshold if agent.threshold is not None else '?'}) "
              f"since {when}")
    for item in files:
        for reader, purposes in sorted(item.readers.items()):
            for name, rec in sorted(purposes.items()):
                at = rec.get("fired_at") if isinstance(rec, dict) else None
                when = _local(at) if isinstance(at, (int, float)) else "?"
                who = "" if reader == "main" else f" agent {reader}"
                print(f"{item.harness} {item.session_id}{who}: {name} {rec.get('mark')} "
                      f"fired {when}")
    return codes.OK


# --- the restart point ------------------------------------------------------------------

def _restart_parser(verbs: Any) -> None:
    restart = verbs.add_parser(
        "restart-point", help="print the measured restart point and the warn-at marks",
        description="Print the restart point measured per model and population, and "
                    "each warn-at mark's threshold in tokens. --measure measures it "
                    "again from the token index first.")
    restart.add_argument("--measure", action="store_true",
                         help="run the index's pass and the measure, and write it")
    restart.add_argument("--json", action="store_true", help="print one JSON object")


def _restart_point(args: argparse.Namespace, prog: str) -> int:
    import moxi.store

    from . import measure

    done = None
    if args.measure:
        try:
            done = measure.run()
        except moxi.store.StoreTooNew as exc:
            print(f"{prog} restart-point: {exc}", file=sys.stderr)
            return codes.REFUSED
        except measure.MeasureError as exc:
            print(f"{prog} restart-point: {exc}", file=sys.stderr)
            return codes.ERROR
    found = measure.restart_point()
    if args.json:
        doc = found.as_dict()
        if done is not None:
            doc["note"] = done.note
        print(json.dumps(doc, indent=2))
    else:
        if done is not None:
            print(done.line())
            if done.note:
                print(f"note: {done.note}")
        for line in restart_lines(found):
            print(line)
    return codes.OK if found.doc is not None else codes.ERROR


def restart_lines(found: Any) -> list[str]:
    """The text of `moxi usage restart-point`: when and over what it was measured, then
    one line per model and population with S, P, g and each mark."""
    from .thresholds import MARKS

    if found.doc is None:
        return [f"restart point: {found.problem}"]
    doc = found.doc
    sample = doc.get("sample") or {}
    lines = [f"measured {doc.get('measured_at')} over the last {doc.get('days')} days: "
             f"{sample.get('files', 0)} files, {sample.get('requests', 0)} requests"
             + ("" if doc.get("interactive_only", True) else "; every main session")]
    if not found.rows:
        lines.append("  no population has a row (each needs 20 files of 5 requests or "
                     "more)")
    for model, population, row, marks in found.rows:
        cells = []
        for mark in MARKS:
            each = marks[mark]
            cells.append(f"{mark} {each.tokens:,}" if each.available else f"{mark} -")
        said = "" if "fallback" not in row else ", its main row"
        w = row.get("w")
        line = (f"  {model:<24} {population:<8} n {row.get('n')}{said}: S {row.get('S'):,.0f}"
                f", P {row.get('P'):,.0f}, g {row.get('g'):,.0f}, "
                f"w {'unknown' if w is None else w}   " + "   ".join(cells))
        lines.append(line)
        reasons = dict.fromkeys(each.reason for each in marks.values() if not each.available)
        for reason in reasons:
            lines.append(f"  {'':<24} {'':<8} unavailable: {reason}")
    for model, populations in sorted((doc.get("thin") or {}).items()):
        for population, count in sorted(populations.items()):
            lines.append(f"  {model:<24} {population:<8} {count} files, under the 20 a "
                         "row needs")
    for model, why in sorted((doc.get("no_w") or {}).items()):
        lines.append(f"  {model:<24} {'':<8} no row: {why}")
    return lines


# --- reports --------------------------------------------------------------------------

REPORT_VERBS = {
    "daily": "tokens and cost by day and model",
    "monthly": "tokens and cost by month and model",
    "sessions": "tokens and cost by session, its subagents included",
    "models": "tokens and cost by model, with where each price came from",
    "projects": "tokens and cost by project, or by directory where no project is known",
}


def _report_verbs(verbs: Any) -> None:
    for verb, text in REPORT_VERBS.items():
        parser = verbs.add_parser(verb, help=text, description=f"Print {text}, from the "
                                  "token index. The index's pass runs first.")
        _report_args(parser)
    terms = verbs.add_parser(
        "terms", help="every term of each cost: tokens x price per class, by model, day, "
                      "month or agent",
        description="Print every term of each cost from the token index: per price "
                    "group, each class's tokens, the price paid and the dollars, then the "
                    "cost split by class and price tier. The index's pass runs first.")
    terms.add_argument("--by", choices=("model", "day", "month", "agent"), default="model",
                       help="group by model (the default), day, month, or the agents of "
                            "--session")
    terms.add_argument("--session", metavar="ID", help="count one session only")
    _report_args(terms)
    requests = verbs.add_parser(
        "requests", help="one line per request of a session's agent, with its context "
                         "and compactions",
        description="Print one line per request of one session's agent from the token "
                    "index: tokens by class, cost, context against the window, turn, and "
                    "where a compaction falls. The index's pass runs first.")
    requests.add_argument("session", metavar="SESSION",
                          help="the session id (a sessions row's id)")
    requests.add_argument("--agent", metavar="ID",
                          help="one of the session's agents (default: main)")
    _report_args(requests)


def _report_args(parser: Any) -> None:
    parser.add_argument("--since", metavar="DATE",
                        help="the first day to count (YYYY-MM-DD)")
    parser.add_argument("--until", metavar="DATE",
                        help="the last day to count (YYYY-MM-DD), included")
    parser.add_argument("--harness", choices=("claude", "codex", "opencode"),
                        help="count one harness only")
    parser.add_argument("--project", metavar="KEY",
                        help="count one project: its key, or the directory a row "
                             "with no project shows")
    parser.add_argument("--model", metavar="MODEL", help="count one model only")
    parser.add_argument("--utc", action="store_true",
                        help="cut days in UTC, not local time")
    parser.add_argument("--no-pass", action="store_true",
                        help="read the index as it is, without running its pass")
    parser.add_argument("--json", action="store_true", help="print one JSON object")


def _report(args: argparse.Namespace, prog: str) -> int:
    from moxi.files import LockTimeout
    from moxi.store import StoreError

    from . import reports

    extra: dict[str, Any] = {}
    if args.verb == "terms":
        extra = {"by": args.by, "session": args.session}
    elif args.verb == "requests":
        extra = {"session": args.session, "agent": args.agent}
    try:
        found = reports.report(args.verb, since=args.since, until=args.until,
                               harness=args.harness, project=args.project,
                               model=args.model, tz="UTC" if args.utc else None,
                               fresh=not args.no_pass, **extra)
    except reports.ReportError as exc:
        print(f"{prog} {args.verb}: {exc}", file=sys.stderr)
        return codes.USAGE
    except LockTimeout as exc:
        print(f"{prog} {args.verb}: {exc}", file=sys.stderr)
        return codes.TEMPFAIL
    except StoreError as exc:
        print(f"{prog} {args.verb}: {exc}", file=sys.stderr)
        return getattr(exc, "exit_code", codes.ERROR)
    if args.json:
        print(json.dumps(found.as_dict(), indent=2))
    elif found.kind == "terms":
        for line in terms_lines(found, args.by):
            print(line)
    elif found.kind == "requests":
        if found.rows:
            for line in requests_lines(found):
                print(line)
    else:
        _print_report(found)
    if found.kind == "requests" and not found.rows:
        agent = found.filters.get("agent", "main")
        print(f"{prog} requests: no request of agent {agent} of session {args.session} "
              "in range", file=sys.stderr)
        note = found.index.get("note")
        if note and not args.json:
            print(note, file=sys.stderr)
        return codes.ERROR
    return codes.OK


#: Each report's label columns: (heading, how to read the row, width).
_LABELS: dict[str, list[tuple[str, Any, int]]] = {
    "daily": [("day", lambda r: r["day"], 10), ("model", lambda r: _model(r["model"]), 28)],
    "monthly": [("month", lambda r: r["month"], 7),
                ("model", lambda r: _model(r["model"]), 28)],
    "sessions": [("session", lambda r: _session(r), 40)],
    "models": [("model", lambda r: _model(r["model"]), 32)],
    "projects": [("project", lambda r: r["project"] or r["cwd"] or "(no directory)", 40)],
}


def _model(model: Any) -> str:
    return model if isinstance(model, str) and model else "(no model)"


def _session(row: dict[str, Any]) -> str:
    sid = row["session_id"] or "(no session id)"
    title = row.get("title")
    return f"{row['harness']} {sid[:8]}" + (f" {title}" if title else "")


def _tokens(value: int) -> str:
    for size, unit in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if value >= size:
            return f"{value / size:.1f}{unit}"
    return str(value)


def _cost(row: dict[str, Any]) -> str:
    return _dollars(row["cost"])


def _marks(row: dict[str, Any]) -> str:
    return ("u" if row["unpriced_tokens"] else "") + ("?" if row["unknown_rows"] else "")


def _print_report(found: Any) -> None:
    labels = _LABELS[found.kind]
    head = [f"{name:<{width}}" for name, _, width in labels]
    numbers = ("requests", "input", "cache_read", "cache_write", "output")
    print("  ".join([*head, *(f"{name:>11}" for name in numbers), f"{'cost':>12}"]))
    for row in found.rows:
        cells = [f"{_cut(str(read(row)), width):<{width}}" for _, read, width in labels]
        cells += [f"{_tokens(row[name]):>11}" for name in numbers]
        cells.append(f"{_cost(row):>12} {_marks(row)}".rstrip())
        print("  ".join(cells))
    total = found.total
    if found.rows:
        width = sum(w for _, _, w in labels) + 2 * (len(labels) - 1)
        cells = [f"{'total':<{width}}"]
        cells += [f"{_tokens(total[name]):>11}" for name in numbers]
        cells.append(f"{_cost(total):>12} {_marks(total)}".rstrip())
        print("  ".join(cells))
    else:
        print("no requests in range")
    for line in _footnotes(found, days=found.kind in ("daily", "monthly")):
        print(line)


def _footnotes(found: Any, *, days: bool) -> list[str]:
    """What a report's figures leave out, the rules that applied, and how it read the
    index."""
    total = found.total
    lines = []
    if total.get("unpriced_tokens"):
        lines.append(f"u: {_tokens(total['unpriced_tokens'])} tokens are unpriced and not "
                     "in the cost; the model table has no price for "
                     f"{', '.join(found.unpriced_models)}. Add a [[model]] row to usage's "
                     "config to price them.")
    if total.get("unknown_rows"):
        lines.append(f"?: {total['unknown_rows']} requests had a token count their harness "
                     "did not report; those totals are at least the sums shown.")
    lines.extend(f"rule: {rule}" for rule in found.rules)
    zone = "local time" if found.tz == "local" else found.tz
    lines.extend(line for line in (f"days in {zone}" if days else None,
                                   found.index.get("note")) if line)
    return lines


#: Where the dollars start on a line of `terms`'s text.
_COST_AT = 49
#: Each term's label in `terms`'s text.
TERM_LABELS = {"input": "input", "cache_read": "cache read",
               "cache_write_5m": "cache write 5m", "cache_write_1h": "cache write 1h",
               "output": "output"}


def terms_lines(found: Any, by: str = "model") -> list[str]:
    """The text of `moxi usage terms`: each row's price groups as a cost's terms (under a
    heading per row, except by model, where the group names its model), a model's total
    under two groups or more, then the total and the split by class and tier."""
    lines: list[str] = []
    indent = "" if by == "model" else "  "
    for row in found.rows:
        if by != "model":
            label = (row[by] if by in ("day", "month")
                     else f"agent {row['agent']} ({row['kind']})")
            lines.append(f"{label}: {_requests(row['requests'])}, "
                         f"{_dollars(row['cost'])} {_marks(row)}".rstrip())
        for group in row["groups"]:
            lines.extend(indent + line for line in _group_lines(group))
        if by == "model" and len(row["groups"]) > 1:
            label = f"{_model(row['model'])} total"
            lines.append(f"{label:<{_COST_AT}}{_dollars(row['cost']):>10}")
    if not found.rows:
        lines.append("no requests in range")
        return lines + _footnotes(found, days=by in ("day", "month"))
    total = found.total
    lines.append(f"total: {_requests(total['requests'])}, {_dollars(total['cost'])} "
                 f"{_marks(total)}".rstrip())
    if total["split"]:
        lines.append("  by class and tier:")
    for entry in total["split"]:
        label = TERM_LABELS[entry["class"]] + (f", {entry['tier']}" if entry["tier"] else "")
        lines.append(f"    {label:<24} {entry['tokens']:>15,}  {_dollars(entry['cost']):>12}")
    return lines + _footnotes(found, days=by in ("day", "month"))


def _group_lines(group: dict[str, Any]) -> list[str]:
    """One price group as a cost's terms: its heading, a line per term, then its cost or
    why it is unpriced."""
    from moxi_sessions.api import UNKNOWN

    provenance = group["provenance"]
    where = [str(group["provider"])] if group["provider"] else []
    if group["tier"]:
        where.append(f"tier {group['tier']}")
    layer = " ".join(str(part) for part in (provenance.get("layer"),
                                            provenance.get("snapshot")) if part)
    where.append(f"{layer} {provenance['rule']}".strip())
    if provenance.get("row") and provenance["row"] != group["model"]:
        where.append(f"row {provenance['row']}")
    if group["period"]:
        period = group["period"]
        where.append(f"from {period.get('from', 'the start')} to {period.get('to', 'now')}")
    head = f"{_model(group['model'])}  {', '.join(where)}"
    lines = [f"{head:<62} {_requests(group['requests']):>17}"]
    filled = set(provenance.get("filled", ()))
    overridden = set(provenance.get("overridden", ()))
    paid = {term["class"]: term for term in group["terms"]}
    for term in group["terms"]:
        price = term["price"]
        rate = "?" if price is UNKNOWN else _rate(price)
        amount = "?" if term["cost"] is UNKNOWN else _dollars(term["cost"])
        how = ""
        if term["price_from"] == "tier-input":
            base = paid["input"]["price"]
            how = (f"   (tier input x {price / base:.6g})"
                   if base is not UNKNOWN and base else "   (tier input)")
        elif term["price_from"] == "row" and group["tier"]:
            how = "   (the row's own price)"
        elif term["price_from"] == "row" and term["class"] in filled:
            how = "   (provider default)"
        elif term["price_from"] == "row" and term["class"] in overridden:
            how = "   (your config)"
        lines.append(f"  {TERM_LABELS[term['class']]:<14} {term['tokens']:>15,} x "
                     f"{rate:>9}/M = {amount:>10}{how}")
    if group["cost"] is UNKNOWN:
        lines.append(f"  unpriced: {group['why']}")
    else:
        lines.append(f"{'  cost':<{_COST_AT}}{_dollars(group['cost']):>10}")
    return lines


#: `requests`'s columns after the label ones: (heading, the row's field, width).
_REQUEST_CLASSES = (("input", "fresh_input", 9), ("cache read", "cache_read", 10),
                    ("write 5m", "cache_write_5m", 9), ("write 1h", "cache_write_1h", 9),
                    ("output", "output", 9))


def requests_lines(found: Any) -> list[str]:
    """The text of `moxi usage requests`: a header for the agent, a line per request,
    a line where a compaction falls, then the total and the report's notes."""
    from moxi_sessions.api import UNKNOWN

    total = found.total
    agent = found.filters.get("agent", "main")
    windows = sorted({row["window"] for row in found.rows if row["window"] is not UNKNOWN})
    window = ("window unknown" if not windows else f"window {windows[0]:,}"
              if len(windows) == 1 else f"window {windows[0]:,} to {windows[-1]:,}")
    turns = total.get("turns")
    count = len(total["marks"])
    lines = [f"agent {agent}: {_requests(total['requests'])}, "
             + ("no turns" if turns is None else f"{turns} turn{'' if turns == 1 else 's'}")
             + f", {count} compaction{'' if count == 1 else 's'}, {window}"]
    head = [f"{'#':>5}", f"{'time':<14}", f"{'turn':>4}", f"{'model':<24}",
            f"{'context':>9}", f"{'%':>4}"]
    head += [f"{name:>{width}}" for name, _, width in _REQUEST_CLASSES]
    lines.append("  ".join([*head, f"{'cost':>10}"]))
    marks: dict[int, list] = {}
    for mark in total["marks"]:
        marks.setdefault(mark["after"], []).append(mark)
    lines.extend(_compaction_line(mark) for mark in marks.get(0, []))
    for row in found.rows:
        context = row["context"]
        percent = ""
        if context is not None and row["window"] is not UNKNOWN:
            percent = f"{100 * context / row['window']:.0f}%"
        cells = [f"{row['n']:>5}", f"{_clock(row['ts'], found.tz):<14}",
                 f"{'' if row['turn'] is None else row['turn']:>4}",
                 f"{_cut(_model(row['model']), 24):<24}",
                 f"{'?' if context is None else _tokens(context):>9}", f"{percent:>4}"]
        cells += [f"{_tokens(row[name]):>{width}}" for _, name, width in _REQUEST_CLASSES]
        cells.append(f"{_dollars(row['cost']):>10}"
                     + (" ?" if row["unknown"] else "") + (" s" if row["synthetic"] else ""))
        lines.append("  ".join(cells).rstrip())
        lines.extend(_compaction_line(mark) for mark in marks.get(row["n"], []))
    lines.append(f"total: {_requests(total['requests'])}, {_dollars(total['cost'])} "
                 f"{_marks(total)}".rstrip())
    if total.get("claimed_elsewhere"):
        lines.append(f"{total['claimed_elsewhere']:,} requests of this agent are counted "
                     "under another file")
    if total.get("copied_marks"):
        lines.append(f"{total['copied_marks']:,} compactions copied from another file "
                     "aren't shown")
    if not total.get("marks_known", True):
        lines.append("compactions aren't read yet for a file of this agent; the next pass "
                     "that can read it brings them in")
    if turns is None:
        lines.append("this harness wrote no turns here")
    if any(row["synthetic"] for row in found.rows):
        lines.append("s: a request the harness made up itself, not sent to a model")
    if any(row["unknown"] for row in found.rows):
        lines.append("?: a token count the harness did not report; that row's figures "
                     "are at least what they show")
    return lines + _footnotes(found, days=False)


def _compaction_line(mark: dict[str, Any]) -> str:
    said = [part for part in (
        mark["trigger"],
        None if mark["pre_tokens"] is None else f"{mark['pre_tokens']:,} before") if part]
    return f"{'':>5}  -- compaction" + (f" ({', '.join(said)})" if said else "") + " --"


def _clock(epoch: Any, tz: str) -> str:
    """A request's time, month and day to the second, in the report's zone."""
    import datetime as dt

    if not isinstance(epoch, (int, float)):
        return "?"
    zone = dt.timezone.utc if tz == "UTC" else None
    moment = dt.datetime.fromtimestamp(epoch, zone)
    return moment.strftime("%m-%d %H:%M:%S")


def _requests(count: int) -> str:
    return f"{count:,} request{'' if count == 1 else 's'}"


def _rate(price: float) -> str:
    return f"${price:,.2f}" if round(price, 2) == price else f"${price:,.6g}"


def _dollars(value: Any) -> str:
    from moxi_sessions.api import UNKNOWN

    if value is UNKNOWN:
        return "unpriced"
    return f"${value:,.2f}" if value >= 1 or value == 0 else f"${value:.4f}"


def _cut(text: str, width: int) -> str:
    return text if len(text) <= width else text[:width - 1] + "~"


# --- index ----------------------------------------------------------------------------

#: How many keys `index --check` names before it counts the rest.
KEYS_SHOWN = 20
#: How many reasons for held rows `index` prints per lane before it counts the rest.
REASONS_SHOWN = 5


def _index_parser(verbs: Any) -> None:
    index = verbs.add_parser(
        "index", help="read what changed in the harnesses' records into the token index",
        description="Run the token index's pass: read every source that changed since the "
                    "last pass, then print the index's status, lane by lane.")
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

    from . import index, reports, store

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
    status = reports.read("status", passed=not result.cancelled)
    differ: list[int] = []
    if args.check:
        conn = store.connect()
        try:
            differ = store.check(conn)
        finally:
            conn.close()
    if args.json:
        doc = {"status": status.as_dict(), "pass": result.as_dict()}
        if args.check:
            doc["check"] = {"agree": not differ, "keys": differ}
        print(json.dumps(doc, indent=2))
    else:
        _print_pass(result)
        _print_status(status)
        if args.check:
            _print_check(differ)
    return codes.ERROR if differ else codes.OK


def _print_pass(result: Any) -> None:
    """What the pass did, in one line, then anything it met that `status` doesn't show."""
    what = "rebuilt" if result.rebuild else "pass"
    print(f"{what}: {result.listed} sources listed, {result.read} read, "
          f"{result.reused} unchanged, {result.excluded} imported, {result.deleted} "
          f"gone, {result.held} held")
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


def _print_status(found: Any) -> None:
    """The `status` report: one line per lane, its reasons under it, then the totals."""
    total = found.total
    for row in found.rows:
        line = (f"  {row['lane']:<16} {row['state'] or '?':<8} {row['files']} files, "
                f"{row['requests']} requests")
        for name in ("duplicates", "excluded", "held"):
            if row[name]:
                line += f", {row[name]} {name}"
        if row.get("compressed"):
            line += f", {row['compressed']} compressed"
        print(line)
        if row.get("reason") and row["state"] != "absent":     # absent: never there
            print(f"  {'':<16} {row['reason']}")
        reasons = list(row["held_reasons"].items())
        for reason, count in reasons[:REASONS_SHOWN]:
            print(f"  {'':<16} held ({count}): {reason}")
        if len(reasons) > REASONS_SHOWN:
            print(f"  {'':<16} held for {len(reasons) - REASONS_SHOWN} more reasons")
    shown = total.get("duplicate_rows") or []
    for dup in shown:
        part = f" ({dup['part']})" if dup["part"] else ""
        of_part = f" ({dup['of_part']})" if dup["of_part"] else ""
        print(f"  duplicate: {dup['lane']} {dup['path']}{part} repeats {dup['of_lane']} "
              f"{dup['of_path']}{of_part}")
    if total.get("duplicate_files", 0) > len(shown):
        print(f"  and {total['duplicate_files'] - len(shown)} more duplicate files")
    last = total["last_pass"]
    when = "never" if last is None else _local(last)
    print(f"index: {total['files']} files, {total['requests']} requests, "
          f"{total['duplicates']} duplicates, {total['excluded']} excluded, "
          f"{total['held']} held; last pass {when}; version {total['index_version']}")
    note = found.index.get("note")
    if note:
        print(note)


def _local(epoch: float) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


def _print_check(differ: list[int]) -> None:
    if not differ:
        print("check: the stored claims agree with the claimed view")
        return
    shown = ", ".join(str(key) for key in differ[:KEYS_SHOWN])
    more = f" and {len(differ) - KEYS_SHOWN} more" if len(differ) > KEYS_SHOWN else ""
    print(f"check: {len(differ)} keys differ from the claimed view: {shown}{more}")
