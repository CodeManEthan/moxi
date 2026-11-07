"""services' page as data: the route table, what the gateway reaches, the marks on every
shape, the budget, the announcement's page and components.

Loopback is reached as the page reaches it, through `moxi.modules.load`. Nothing here runs
a handler: `gate_matrix` replaces every target, and the export resolves none.
"""

from __future__ import annotations

import subprocess
import sys

from moxi_services import page, rows

from page_kit import LB


# -- the table ------------------------------------------------------------------------------

READS = {"/api/rows", "/api/row", "/api/projects", page.STATUS_ROUTE, "/api/events"}
CONFIRMED = {"/api/start", "/api/stop", "/api/restart", "/api/run", "/api/enable",
             "/api/disable", "/api/remove"}
#: The routes the phone reaches: the reads but the project list, the stream, the status
#: route and every confirmed act on a row but remove.
GATEWAY = (READS - {"/api/projects"}) | (CONFIRMED - {"/api/remove"})


def routes() -> dict[str, dict]:
    return {r["path"]: r for r in LB.export(page.PAGE)["routes"]}


def test_the_gate_matrix_passes():
    assert LB.gate_matrix(page.PAGE)


def test_the_page_declares_its_reads_its_confirmed_acts_and_add():
    found = routes()
    assert {p for p, r in found.items() if r["kind"] == "read"} == READS
    assert {p for p, r in found.items() if r["kind"] == "act"} == CONFIRMED | {"/api/add"}
    assert {p for p, r in found.items() if r["confirmed"]} == CONFIRMED
    assert {p for p, r in found.items() if r["stream"]} == {"/api/events"}
    for path in CONFIRMED:
        assert found[path]["status"] == page.STATUS_ROUTE, path
    assert found["/api/add"]["status"] is None


def test_the_gateway_reaches_the_reads_the_stream_the_status_and_six_acts():
    found = routes()
    assert {p for p, r in found.items() if r["gateway"]} == GATEWAY
    for path in ("/api/add", "/api/remove", "/api/projects"):
        assert found[path]["gateway"] is False, path


def test_the_stream_has_one_topic_carrying_what_the_rows_read_answers():
    found = routes()["/api/events"]
    assert set(found["topics"]) == {"rows"} == {page.TOPIC}
    assert found["topics"]["rows"] == {"type": "table", "mark": None, "nullable": False,
                                       "items": routes()["/api/rows"]["returns"]}


def _leaves(shape: dict, at: str, marked: str | None = None):
    """Each string leaf of an exported shape, by its dotted place (`[]` a list's item),
    with the nearest mark on its way down."""
    mark = shape.get("mark") or marked
    if shape["type"] == "string":
        yield at, mark
    elif shape["type"] == "list":
        yield from _leaves(shape["items"], at + "[]", mark)
    elif shape["type"] == "table":
        for key, sub in shape["items"].items():
            yield from _leaves(sub, f"{at}.{key}", mark)


def every_leaf() -> dict[str, str | None]:
    out = {}
    for path, route in routes().items():
        if route["stream"]:
            for name, shape in route["topics"].items():
                out.update({f"{path} {k}": v for k, v in _leaves(shape, name)})
        else:
            for key, shape in route["returns"].items():
                out.update({f"{path} {k}": v for k, v in _leaves(shape, key)})
    return out


#: Within one row: the string leaves that are a name, an id, a time or a word of a fixed
#: set, and so carry no mark.
ROW_PLAIN = {"id", "module", "name", "form", "kind", "project", "switch", "verdict.state",
             "state", "since", "last_run.at", "checked.state", "unit", "schedule",
             "stopped", "platform"}
ROW_REDACT = {"doc", "verdict.reason", "verdict.fix", "detail", "last_run.text",
              "checked.line", "argv[]", "health[]"}


def test_every_string_leaf_is_plain_only_for_a_name_an_id_a_time_or_a_word():
    plain = {f"{where} {row}.{leaf}"
             for where, row in (("/api/rows", "rows[]"), ("/api/events", "rows.rows[]"),
                                ("/api/row", "row"))
             for leaf in ROW_PLAIN}
    plain |= {"/api/rows state", "/api/rows problems[].module",
              "/api/events rows.state", "/api/events rows.problems[].module",
              "/api/row runs[].at", "/api/row runs[].result",
              "/api/projects projects[].key",
              "/api/status job", "/api/status act", "/api/status id", "/api/status state"}
    found = every_leaf()
    assert {k for k, v in found.items() if v is None} == plain


def test_commands_are_redact_a_working_directory_path_and_every_sentence_redact():
    redact = {f"{where} {row}.{leaf}"
              for where, row in (("/api/rows", "rows[]"), ("/api/events", "rows.rows[]"),
                                 ("/api/row", "row"))
              for leaf in ROW_REDACT}
    redact |= {"/api/rows detail", "/api/rows problems[].reason",
               "/api/events rows.detail", "/api/events rows.problems[].reason",
               "/api/row log", "/api/projects reason",
               "/api/status lines[]", "/api/status fix"}
    path = {f"{where} {row}.cwd"
            for where, row in (("/api/rows", "rows[]"), ("/api/events", "rows.rows[]"),
                               ("/api/row", "row"))} | {"/api/projects projects[].root"}
    found = every_leaf()
    assert {k for k, v in found.items() if v == "redact"} == redact
    assert {k for k, v in found.items() if v == "path"} == path


def test_a_command_list_is_redact_itself_not_only_each_word():
    row = routes()["/api/rows"]["returns"]["rows"]["items"]["items"]
    for key in ("argv", "health"):
        assert row[key]["mark"] == "redact" and row[key]["items"]["mark"] == "redact"


def test_the_page_spells_kinds_and_days_as_the_rows_do():
    assert page.KINDS == rows.KINDS and page.DAYS == rows.DAYS


def test_the_export_carries_the_budget():
    assert LB.export(page.PAGE)["budget"] == page.BUDGET == {"rss": "110M", "fds": 48}


def test_the_announcement_offers_the_page_and_imports_none_of_it():
    from moxi_services import module

    assert module.PAGE == "moxi_services.page:PAGE"
    assert "ui" in module.OPTIONAL
    assert not hasattr(module, "BUDGETS")
    probe = ("import sys, moxi_services.module, moxi_services.cli, moxi_services.doctor, "
             "moxi_services.projects_link\n"
             "print(sorted(m for m in sys.modules if m.startswith(('moxi_ui', "
             "'moxi_services.page', 'moxi_services.jobs'))))")
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                          timeout=60, check=True)
    assert done.stdout.strip() == "[]"


def test_the_components_are_the_page_and_the_home_summary_block():
    from moxi_services import module

    assert set(module.COMPONENTS) == {"page", "summary"}
    for name, decl in module.COMPONENTS.items():
        assert decl["script"] == f"{name}.js"
        assert decl["contract"] == 1
    assert module.COMPONENTS["page"]["place"] == "page"
    summary = module.COMPONENTS["summary"]
    assert summary["place"] == "block" and summary["home"] is True
